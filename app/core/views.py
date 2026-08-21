"""Cross-cutting surfaces: the health probe, the entry redirect and the design
token reference. Product screens live in their own domain modules.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError, connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static


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


def favicon(request: HttpRequest) -> HttpResponse:
    """Answer `/favicon.ico` with the brand mark.

    Browsers request this path whether or not the page links an icon, so
    without it every single visit logged a `Not Found: /favicon.ico` warning —
    noise that makes a real 404 harder to notice in the log.

    A redirect rather than a checked-in `.ico`: the brand PNG already exists and
    is already served with the right cache headers, and drawing a second copy of
    a logo into the repository is a maintenance liability for no gain.
    Resolved per request rather than at import, so the hashed static name is
    looked up from whatever manifest the running process actually has.
    """
    return redirect(static("brand/koda-logo-negative.png"), permanent=False)


def home(request: HttpRequest) -> HttpResponse:
    """The root is a doorway, not a page.

    A signed-in lawyer wants the work list, not a welcome screen; anyone else
    needs to sign in first.
    """
    from app.accounts import shared_gate

    if request.user.is_authenticated or shared_gate.has_passed(request):
        # Ülevaade rather than Minu töö: the first question on opening the
        # application is what is happening across the department, and the
        # personal queue is one click away. In shared-gate mode that is also
        # true of somebody who has not chosen a persona — the department is
        # exactly what they can see (Stage-2D auth brief 6).
        return redirect("matters:overview")
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
