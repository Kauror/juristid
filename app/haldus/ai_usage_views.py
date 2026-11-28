"""Haldus → Integratsioonid → AI kasutus (r98, docs/adr/0235).

Read-only, for a verified technical administrator (the same predicate as Lovable
and Süsteemi seis): costs and request metadata are operational, not for every
lawyer. Reads only the ledger; never calls a provider. Prompts, answers and
document text are not in the ledger and so cannot be shown here.
"""

from __future__ import annotations

import csv
import datetime as dt
from typing import Any

from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from app.ai_usage import report
from app.ai_usage.models import AiAttempt, AttemptOutcome, Service
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
    if outcome not in AttemptOutcome.values:
        outcome = ""
    return {
        "key": key,
        "start": start,
        "end": end,
        "service": service,
        "model": request.GET.get("mudel", "")[:80],
        "outcome": outcome,
    }


def _attempts(f: dict[str, Any]) -> Any:
    first, after = report.day_bounds(f["start"], f["end"])
    found = AiAttempt.objects.filter(started_at__gte=first, started_at__lt=after).select_related(
        "operation"
    )
    if f["service"]:
        found = found.filter(operation__service=f["service"])
    if f["model"]:
        found = found.filter(actual_model=f["model"])
    if f["outcome"]:
        found = found.filter(outcome=f["outcome"])
    return found.order_by("-started_at", "-sequence")


def _row(book: report.PriceBook, attempt: AiAttempt) -> dict[str, Any]:
    cost = report.attempt_cost(book, attempt)
    return {
        "attempt": attempt,
        "cost": report.money(cost.eur),
        "lower_bound": cost.lower_bound,
        "run_ref": attempt.operation.run_ref,
    }


@never_cache
@require_http_methods(["GET"])
def ai_usage(request: HttpRequest) -> HttpResponse:
    _require(request, "ai_kasutus")
    f = _filters(request)
    book = report.PriceBook.load()
    summary = report.summarise(
        f["start"], f["end"], service=f["service"], model=f["model"], book=book
    )
    today = report.local_date(timezone.now())
    day = report.summarise(today, today, book=book).totals
    month = report.budget(book=book)
    page = Paginator(_attempts(f), PAGE_SIZE).get_page(request.GET.get("leht"))
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
            outcomes=AttemptOutcome.choices,
            summary=summary,
            today_cost=report.money(day.cost_eur if not day.unknown_cost_attempts else None)
            if day.attempts
            else report.money(day.cost_eur),
            today_unknown=day.unknown_cost_attempts,
            month=month,
            month_cost=report.money(month.spent_eur),
            note=report.coverage_note(summary),
            page=page,
            rows=rows,
            peak=peak,
            query=query.urlencode(),
            day_rows=[(d, t, report.money(t.cost_eur)) for d, t in summary.by_day.items()],
            service_rows=[
                (
                    dict(Service.choices).get(name, name),
                    t,
                    report.money(t.cost_eur),
                    summary.durations_by_service.get(name),
                )
                for name, t in sorted(summary.by_service.items())
            ],
            model_rows=[
                (name, t, report.money(t.cost_eur)) for name, t in sorted(summary.by_model.items())
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
            "Kestus ms",
            "Sisend",
            "Väljund",
            "Vahemälu lugemine",
            "Vahemälu loomine",
            "Hinnanguline maksumus EUR",
            "Käivitus",
        ]
    )
    for attempt in _attempts(f)[:5000]:
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
                    "" if attempt.duration_ms is None else attempt.duration_ms,
                    "" if attempt.input_tokens is None else attempt.input_tokens,
                    "" if attempt.output_tokens is None else attempt.output_tokens,
                    "" if attempt.cache_read_tokens is None else attempt.cache_read_tokens,
                    "" if attempt.cache_creation_tokens is None else attempt.cache_creation_tokens,
                    "teadmata" if cost.eur is None else f"{cost.eur:.4f}",
                    attempt.operation.run_ref,
                ]
            )
        )
    return response
