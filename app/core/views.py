"""Cross-cutting surfaces: the health probe, the entry redirect and the design
token reference. Product screens live in their own domain modules.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError, connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render


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
    """The root is a doorway, not a page.

    A signed-in lawyer wants the work list, not a welcome screen; anyone else
    needs to sign in first.
    """
    if request.user.is_authenticated:
        return redirect("matters:my_work")
    return render(request, "core/home.html")


SURFACE_TOKENS = (
    "--surface-base",
    "--surface-nav",
    "--surface-raised",
    "--surface-panel",
    "--surface-elevated",
    "--surface-hover",
    "--surface-selected",
    "--surface-document",
)

TEXT_TOKENS = (
    "--text-primary",
    "--text-body",
    "--text-secondary",
    "--text-muted",
    "--accent-link",
)


@login_required
def design_tokens(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "core/design_tokens.html",
        {"surface_tokens": SURFACE_TOKENS, "text_tokens": TEXT_TOKENS},
    )
