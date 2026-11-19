"""Haldus → Registrite jälgimine: the nightly register watch's log (ADR 0217 §8).

Read-only. Three pages: the runs, newest first, with what each read, found and
cost in model tokens; one run's documents and errors; and every source the
watch reads. Opens for whoever reads business content, like the lawyer's own
review page (`register_watch:review`). A Teema, its search words and its
documents are shown only through `Matter.objects.visible_to`, so a restricted
Teema's title never reaches a reader who may not see it; a run's totals are
counts and show nothing of one.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from django.db.models import Count, Q, QuerySet, Sum
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from app.haldus.views import NOT_HERE, _context, _require
from app.matters.models import Matter
from app.register_watch.models import FindingStatus, WatchFinding, WatchRun, WatchTarget
from app.register_watch.watch import watch_health

RUNS_SHOWN = 60
TOTAL_DAYS = 30


def _pages(active: str) -> list[dict[str, Any]]:
    return [
        {
            "label": "Käivitused",
            "url": reverse("haldus:register_watch"),
            "active": active == "runs",
        },
        {
            "label": "Allikad",
            "url": reverse("haldus:register_watch_sources"),
            "active": active == "sources",
        },
    ]


def _visible(request: HttpRequest) -> QuerySet[Matter]:
    return Matter.objects.visible_to(request.user)


def _duration(run: WatchRun) -> str:
    if run.finished_at is None:
        return ""
    seconds = int((run.finished_at - run.started_at).total_seconds())
    minutes, seconds = divmod(max(seconds, 0), 60)
    return f"{minutes} min {seconds} s" if minutes else f"{seconds} s"


@never_cache
@require_http_methods(["GET"])
def runs(request: HttpRequest) -> HttpResponse:
    """Every run, newest first, and the last thirty days in sum."""
    _require(request, "registrid")
    rows = list(WatchRun.objects.order_by("-started_at")[:RUNS_SHOWN])
    since = timezone.now() - dt.timedelta(days=TOTAL_DAYS)
    totals = WatchRun.objects.filter(started_at__gte=since).aggregate(
        runs=Count("pk"),
        offered=Sum("findings_offered"),
        calls=Sum("ai_calls"),
        judged=Sum("ai_judged"),
        failures=Sum("ai_failures"),
        input_tokens=Sum("ai_input_tokens"),
        output_tokens=Sum("ai_output_tokens"),
    )
    return render(
        request,
        "haldus/register_watch.html",
        _context(
            request,
            "registrid",
            pages=_pages("runs"),
            runs=[{"run": run, "duration": _duration(run)} for run in rows],
            latest=rows[0] if rows else None,
            health=watch_health(),
            totals=totals,
            total_days=TOTAL_DAYS,
            waiting=WatchFinding.objects.filter(
                status=FindingStatus.PENDING, matter__in=_visible(request)
            ).count(),
        ),
    )


@never_cache
@require_http_methods(["GET"])
def run(request: HttpRequest, pk: Any) -> HttpResponse:
    """One run: the documents it found on Teemad the reader may see, and its errors."""
    _require(request, "registrid")
    record = WatchRun.objects.filter(pk=pk).first()
    if record is None:
        raise Http404(NOT_HERE)
    window = Q(created_at__gte=record.started_at)
    if record.finished_at is not None:
        window &= Q(created_at__lte=record.finished_at)
    found = WatchFinding.objects.filter(window, matter__in=_visible(request))
    references = set(
        _visible(request)
        .filter(watch_targets__isnull=False)
        .distinct()
        .values_list("reference_year", "reference_number")
    )
    shown_references = {f"{year}_{number}" for year, number in references}
    errors = [
        error
        for error in record.errors
        if not error.get("teema") or error.get("teema") in shown_references
    ]
    return render(
        request,
        "haldus/register_watch_run.html",
        _context(
            request,
            "registrid",
            pages=_pages(""),
            run=record,
            duration=_duration(record),
            findings=list(
                found.exclude(status=FindingStatus.BASELINE)
                .select_related("matter", "target")
                .order_by("matter__reference_year", "matter__reference_number", "-occurred_on")
            ),
            earlier=found.filter(status=FindingStatus.BASELINE).count(),
            errors=errors,
        ),
    )


@never_cache
@require_http_methods(["GET"])
def sources(request: HttpRequest) -> HttpResponse:
    """Every register search the watch runs, per Teema, with its last check."""
    _require(request, "registrid")
    targets = (
        WatchTarget.objects.filter(matter__in=_visible(request))
        .select_related("matter")
        .annotate(
            found=Count("findings", filter=~Q(findings__status=FindingStatus.BASELINE)),
        )
        .order_by("matter__reference_year", "matter__reference_number", "source", "identifier")
    )
    rows = list(targets)
    return render(
        request,
        "haldus/register_watch_sources.html",
        _context(
            request,
            "registrid",
            pages=_pages("sources"),
            targets=rows,
            failing=sum(1 for target in rows if target.last_error),
        ),
    )
