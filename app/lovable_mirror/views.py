"""The reconciliation preview: one read-only page for the administrator.

Under `/haldus/` with the other migration tooling, on no navigation bar
(CONTRACT §1; the coordinator decides any nav entry). The gate is the one the
historical reconciliation queue uses — `may_review_historical_matches`, the
ADMINISTRATOR acting as themselves — and everybody else gets 404, the
convention every restricted surface here follows: a 403 would confirm the page
exists (app/core/views.py `development_status`).

GET only. There is no form, no button that writes and no refresh action: a
live refresh stays disabled until an authorised reader exists
(app/lovable_mirror/readers.py), and an import is an operator command.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from app.core.authorization import may_review_historical_matches
from app.lovable_mirror import catalogue
from app.lovable_mirror.readers import LIVE_READER_SETTING, live_reader_enabled
from app.lovable_mirror.reconcile import build_report

#: Rows rendered per list. The counts above each list are the whole figure.
LIST_LIMIT = 200


def may_view_mirror(user: object) -> bool:
    return may_review_historical_matches(user)


@login_required
@require_GET
def preview(request: HttpRequest) -> HttpResponse:
    if not may_view_mirror(request.user):
        raise Http404("Lovable'i peegel on halduse töövahend.")
    report = build_report(request.user)
    return render(
        request,
        "lovable_mirror/preview.html",
        {
            "report": report,
            "limit": LIST_LIMIT,
            "catalogue": catalogue.all_field_mappings(),
            "live_reader_enabled": live_reader_enabled(),
            "live_reader_setting": LIVE_READER_SETTING,
            "nav_active": "haldus",
        },
    )
