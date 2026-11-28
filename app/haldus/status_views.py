"""Haldus → Integratsioonid → Öised tsüklid and Allikate värskus (r98, docs/adr/0237).

Read-only, for a verified technical administrator. Both pages only lay out records the steps
already keep (`app.core.integration_status`); neither calls a source or a provider.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from app.core import integration_status, nightly_outcome
from app.core.integration_status import STEP_LABELS
from app.haldus.register_watch_views import NIGHTLY_OUTCOME_LABELS
from app.haldus.views import _context, _require


@never_cache
@require_http_methods(["GET"])
def nightly_history(request: HttpRequest) -> HttpResponse:
    _require(request, "oised_tsuklid")
    nights = integration_status.history(30)
    return render(
        request,
        "haldus/nightly_history.html",
        _context(
            request,
            "oised_tsuklid",
            nights=nights,
            steps=[(s, STEP_LABELS[s]) for s in nightly_outcome.STEPS],
            outcome_labels=NIGHTLY_OUTCOME_LABELS,
        ),
    )


@never_cache
@require_http_methods(["GET"])
def freshness(request: HttpRequest) -> HttpResponse:
    _require(request, "varskus")
    rows = integration_status.sources()
    return render(
        request,
        "haldus/freshness.html",
        _context(
            request,
            "varskus",
            sources=rows,
            attention=integration_status.attention(rows),
        ),
    )


@never_cache
@require_http_methods(["GET"])
def processing(request: HttpRequest) -> HttpResponse:
    """Dokumentide töötlus ja OneNote'i katvus: counts and ages only (docs/adr/0239)."""
    from app.documents import processing_health
    from app.legacy_import import onenote_diagnostics

    _require(request, "toodeldud")
    health = processing_health.collect()
    waiting = health.oldest_waiting_age
    return render(
        request,
        "haldus/processing.html",
        _context(
            request,
            "toodeldud",
            health=health,
            waiting_hours=(waiting.seconds // 3600) if waiting else 0,
            failures=[
                (processing_health.KIND_LABELS[kind], n)
                for kind, n in sorted(health.failures.items())
            ],
            onenote=onenote_diagnostics.diagnostics(),
        ),
    )
