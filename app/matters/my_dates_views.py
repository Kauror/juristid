"""Minu tähtajad and Minu jõustumised — the signed-in person's own dates.

Self only, like Minu statistika: the routes take no person, so a department
head opening either page reads their own dates, and there is no address that
would show anybody else's. Read-only: every «Muuda» is a link to the form that
already edits the record on its Teema (`app/matters/my_dates.py`).
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from app.matters import my_dates


@login_required
@require_GET
def my_deadlines(request: HttpRequest) -> HttpResponse:
    """Minu tähtajad — dated work on the person's own open Teemad."""
    page = my_dates.build_my_deadlines(
        request.user, request.GET, request.path, timezone.localdate()
    )
    return render(
        request,
        "matters/my_deadlines.html",
        {
            "page": page,
            "calendar": page.calendar,
            "nav_active": "minu_asjad",
            "my_tab": "tahtajad",
        },
    )


@login_required
@require_GET
def my_effective_dates(request: HttpRequest) -> HttpResponse:
    """Minu jõustumised — commencements on the person's own Teemad, open and closed."""
    page = my_dates.build_my_effective_dates(
        request.user, request.GET, request.path, timezone.localdate()
    )
    return render(
        request,
        "matters/my_effective_dates.html",
        {
            "page": page,
            "calendar": page.calendar,
            "nav_active": "minu_asjad",
            "my_tab": "joustumised",
        },
    )
