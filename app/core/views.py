"""Cross-cutting surfaces: the health probe, the entry redirect and the design
token reference. Product screens live in their own domain modules.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError, connection
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static

from app.core.authorization import is_department_head
from app.core.development_status import ITEMS as DEVELOPMENT_STATUS_ITEMS


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

    **Minu asjad is a person's default home.** The first question on opening
    the application is "what do I have to do now", and the department view is
    one click away on the bar — the reverse of the Stage-2D arrangement, which
    opened Ülevaade and left the personal queue to be found. Ülevaade is
    unchanged and still first-class; only what `/` *chooses* moved.

    That destination needs a person, so the shared gate gets the step it was
    missing: somebody who has typed the department password but not said whose
    work they are looking at is sent to the persona selector rather than to a
    page built for nobody. Password → choose a persona → Minu asjad, with no
    detour through a page they did not ask for.

    Deliberately a redirect to the canonical route rather than a second copy of
    the page: `/minu-asjad/` stays the address that gets bookmarked, linked and
    resolved (docs/adr/0016, "the landing page" section).
    """
    from app.accounts import shared_gate

    if request.user.is_authenticated:
        return redirect("matters:my_work")
    if shared_gate.has_passed(request):
        # Not `matters:my_work`: that page is one person's desk and there is no
        # person yet. Not `matters:overview` either — the department view is a
        # destination somebody chooses, not the thing standing between them and
        # their own work.
        return redirect("accounts:choose_persona")
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


@login_required
def development_status(request: HttpRequest) -> HttpResponse:
    """What the v2 rebuild could not settle, for the people who can settle it.

    Under `/haldus/`, with the rest of the internal tooling, and not on any
    navigation bar: this is a worklist about the build, not a product screen. It
    renders `app/core/development_status.py` and nothing else — there is no
    Matter, no member and no company content on it, so it needs no visibility
    scoping of its own.

    The gate is the department head or the technical administrator, both read
    from rules that already exist. 404 rather than 403, the convention every
    other restricted surface in this application follows: a 403 would confirm
    the page is there and that somebody else may read it.
    """
    from app.legacy_import.opinion_access import may_use_opinion_queue

    if not (is_department_head(request.user) or may_use_opinion_queue(request.user)):
        raise Http404("Arendusseis on halduse töövahend.")

    return render(
        request,
        "core/development_status.html",
        {"items": DEVELOPMENT_STATUS_ITEMS, "nav_active": "haldus"},
    )
