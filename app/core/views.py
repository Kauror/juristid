"""Stage-0 surfaces only: a health probe, a placeholder landing page and the
design-token reference. No product screens exist yet.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError, connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render


def healthz(request: HttpRequest) -> JsonResponse:
    database = "ok"
    status = 200
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError as exc:  # pragma: no cover - exercised by compose smoke test
        database = f"error: {exc.__class__.__name__}"
        status = 503

    return JsonResponse(
        {
            "status": "ok" if status == 200 else "degraded",
            "database": database,
            "environment": settings.APPLICATION_ENVIRONMENT,
            "stage": settings.APPLICATION_STAGE,
            "revision": settings.APPLICATION_REVISION,
        },
        status=status,
    )


def home(request: HttpRequest) -> HttpResponse:
    return render(request, "core/home.html")


SURFACE_TOKENS = (
    "--surface-canvas",
    "--surface-primary",
    "--surface-raised",
    "--surface-overlay",
    "--surface-selected",
    "--surface-hover",
    "--surface-document",
)

TEXT_TOKENS = (
    "--text-primary",
    "--text-secondary",
    "--text-muted",
    "--text-link",
)


@login_required
def design_tokens(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "core/design_tokens.html",
        {"surface_tokens": SURFACE_TOKENS, "text_tokens": TEXT_TOKENS},
    )
