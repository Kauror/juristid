"""Haldus → Integratsioonid → AI kasutus (r98, docs/adr/0236).

Read-only, for a verified technical administrator (the same predicate as Lovable
and Süsteemi seis): costs and request metadata are operational, not for every
lawyer. Reads only the ledger; never calls a provider. Prompts, answers and
document text are not in the ledger and so cannot be shown here.
"""

from __future__ import annotations

import csv
import datetime as dt
from typing import Any
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from app.ai_usage import report
from app.ai_usage.models import AiAttempt, Outcome, Service
from app.core.csv_safety import csv_safe_row
from app.haldus.views import _context, _require

PAGE_SIZE = 50
PERIODS = (
    ("today", "Täna"),
    ("7d", "Viimased 7 päeva"),
    ("month", "Jooksev kuu"),
    ("prev_month", "Eelmine kuu"),
    ("custom", "Oma ajavahemik"),
)


def _date(raw: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(raw or "")
    except ValueError:
        return None


def _filters(request: HttpRequest) -> dict[str, Any]:
    key = request.GET.get("periood", "month")
    if key not in {name for name, _ in PERIODS}:
        key = "month"
    custom = None
    first, last = _date(request.GET.get("alates")), _date(request.GET.get("kuni"))
    if first and last:
        custom = (first, last)
    elif key == "custom":
        key = "month"
    start, end = report.period(key, custom=custom)
    service = request.GET.get("teenus", "")
    if service not in Service.values:
        service = ""
    outcome = request.GET.get("tulemus", "")
    if outcome not in Outcome.values:
        outcome = ""
    return {
        "key": key,
        "start": start,
        "end": end,
        "service": service,
        "model": request.GET.get("mudel", "")[:80],
        "outcome": outcome,
    }


ROW_LIMIT = 20000


def _summary(f: dict[str, Any], book: report.PriceBook) -> report.Summary:
    """The one population: the page, the charts, the detail table and the export all read it."""
    return report.summarise(
        f["start"],
        f["end"],
        service=f["service"],
        model=f["model"],
        outcome=f["outcome"],
        book=book,
    )


def _drill(f: dict[str, Any], day: dt.date) -> str:
    """A day's own page with the filters that apply kept."""
    params = {"periood": "custom", "alates": day.isoformat(), "kuni": day.isoformat()}
    for key, name in (("service", "teenus"), ("model", "mudel"), ("outcome", "tulemus")):
        if f[key]:
            params[name] = f[key]
    return "?" + urlencode(params)


def _series(key: str, title: str, pairs: list[tuple[str, int]]) -> dict[str, Any]:
    """A trend in the reporting charts' geometry from (label, whole-number value) pairs."""
    from app.reporting import charts

    maximum = max((value for _, value in pairs), default=0) or 1
    span = charts.TREND_WIDTH - charts.TREND_PAD_LEFT - charts.TREND_PAD_RIGHT
    plot = charts.TREND_HEIGHT - charts.TREND_PAD_TOP - charts.TREND_PAD_BOTTOM
    divisor = max(len(pairs) - 1, 1)
    points = tuple(
        charts.Point(
            x=charts.TREND_PAD_LEFT + span * i / divisor,
            y=charts.TREND_PAD_TOP + plot * (1 - value / maximum),
            label=label,
            value=value,
            url="",
        )
        for i, (label, value) in enumerate(pairs)
    )
    line = " ".join(f"{p.x:.1f},{p.y:.1f}" for p in points)
    total = sum(value for _, value in pairs)
    return {
        "key": key,
        "title": title,
        "summary": f"{len(pairs)} päeva, kokku {total}.",
        "trend": charts.Trend(points=points, polyline=line, maximum=maximum),
    }


def _row(book: report.PriceBook, attempt: AiAttempt) -> dict[str, Any]:
    cost = report.attempt_cost(book, attempt)
    return {
        "attempt": attempt,
        "cost": report.money(cost.eur),
        "lower_bound": cost.lower_bound,
        "stale": cost.stale_price or cost.stale_rate,
        "price_id": cost.price_id,
        "run_ref": attempt.operation.run_ref,
    }


@never_cache
@require_http_methods(["GET"])
def ai_usage(request: HttpRequest) -> HttpResponse:
    _require(request, "ai_kasutus")
    f = _filters(request)
    book = report.PriceBook.load()
    summary = _summary(f, book)
    today = report.local_date(timezone.now())
    day = report.summarise(today, today, book=book).totals
    month = report.budget(book=book)
    page = Paginator(report.attempts_of(summary, f["model"]), PAGE_SIZE).get_page(
        request.GET.get("leht")
    )
    rows = [_row(book, attempt) for attempt in page.object_list]
    peak = max((t.cost_eur for t in summary.by_day.values()), default=0) or 1
    query = request.GET.copy()
    query.pop("leht", None)
    return render(
        request,
        "haldus/ai_usage.html",
        _context(
            request,
            "ai_kasutus",
            f=f,
            periods=PERIODS,
            services=Service.choices,
            outcomes=[c for c in Outcome.choices if c[0] != Outcome.IN_PROGRESS]
            + [(Outcome.IN_PROGRESS.value, Outcome.IN_PROGRESS.label)],
            row_limit=ROW_LIMIT,
            summary=summary,
            # The measured part, always; the unknown part is said beside it, never folded in.
            today_cost=report.known_money(day) if day.attempts else "Täna päringuid pole",
            today=day,
            month=month,
            month_cost=report.money(month.known_eur),
            period_cost=report.known_money(summary.totals),
            reason_labels=summary.totals.incomplete_labels,
            note=report.coverage_note(summary),
            page=page,
            rows=rows,
            peak=peak,
            charts=[
                _series(
                    "cost",
                    "Teadaolev maksumus päevas (eurosenti; teadmata hinnaga päringuid pole sees)",
                    [
                        (f"{d.day}.{d.month}", int(t.cost_eur * 100))
                        for d, t in summary.by_day.items()
                    ],
                ),
                _series(
                    "volume",
                    "Füüsiliste katsete arv päevas (päev = päringu algus)",
                    [(f"{d.day}.{d.month}", t.attempts) for d, t in summary.by_day.items()],
                ),
                _series(
                    "failures",
                    "Ebaõnnestunud füüsilised katsed päevas",
                    [(f"{d.day}.{d.month}", t.failed_attempts) for d, t in summary.by_day.items()],
                ),
                _series(
                    "retries",
                    "Korduskatsed päevas",
                    [(f"{d.day}.{d.month}", t.retried_attempts) for d, t in summary.by_day.items()],
                ),
            ],
            query=query.urlencode(),
            day_rows=[
                (d, t, report.known_money(t), _drill(f, d)) for d, t in summary.by_day.items()
            ],
            service_rows=[
                (
                    dict(Service.choices).get(name, name),
                    t,
                    report.known_money(t),
                    summary.durations_by_service.get(name),
                )
                for name, t in sorted(summary.by_service.items())
            ],
            model_rows=[
                (name, t, report.known_money(t)) for name, t in sorted(summary.by_model.items())
            ],
        ),
    )


@never_cache
@require_http_methods(["GET"])
def ai_usage_csv(request: HttpRequest) -> HttpResponse:
    """The detail table as CSV: metadata only, every cell through `csv_safe_row`."""
    _require(request, "ai_kasutus")
    f = _filters(request)
    book = report.PriceBook.load()
    summary = _summary(f, book)
    queryset = report.attempts_of(summary, f["model"])
    total_rows = queryset.count()
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="ai-kasutus.csv"'
    writer = csv.writer(response, delimiter=";")
    writer.writerow(
        [
            "Algus",
            "Teenus",
            "Mudel (tegelik)",
            "Mudel (küsitud)",
            "Katse",
            "Tulemus",
            "HTTP",
            "Aeg vastuse päisteni ms",
            "Sisend",
            "Väljund",
            "Vahemälu lugemine",
            "Vahemälu loomine",
            "Hinnanguline maksumus EUR",
            "Käivitus",
            "Hinnarida",
            "Kursi kuupäev",
            "Maksumus on alampiir",
        ]
    )
    for attempt in queryset[:ROW_LIMIT]:
        cost = report.attempt_cost(book, attempt)
        writer.writerow(
            csv_safe_row(
                [
                    attempt.started_at.astimezone(report.TALLINN).isoformat(timespec="seconds"),
                    attempt.operation.service,
                    attempt.actual_model,
                    attempt.requested_model,
                    attempt.sequence,
                    attempt.outcome,
                    attempt.http_status or "",
                    "" if attempt.headers_ms is None else attempt.headers_ms,
                    "" if attempt.input_tokens is None else attempt.input_tokens,
                    "" if attempt.output_tokens is None else attempt.output_tokens,
                    "" if attempt.cache_read_tokens is None else attempt.cache_read_tokens,
                    "" if attempt.cache_creation_tokens is None else attempt.cache_creation_tokens,
                    "teadmata" if cost.eur is None else f"{cost.eur:.4f}",
                    attempt.operation.run_ref,
                    cost.price_id or "",
                    cost.rate_on.isoformat() if cost.rate_on else "",
                    "jah" if cost.lower_bound or cost.stale_price or cost.stale_rate else "",
                ]
            )
        )
    if total_rows > ROW_LIMIT:
        # The export says so itself: a silently cut file would read as the whole population.
        writer.writerow(
            csv_safe_row([f"KATKESTATUD: {ROW_LIMIT} rida {total_rows}-st; kitsenda filtreid"])
        )
    return response
