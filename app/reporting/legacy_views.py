"""The «vana Exceli formaat» download, kept apart from the Statistika views.

A thin view and nothing else: ``@login_required`` and ``viewer_for`` exactly as
the CSV ``export`` view in ``app.reporting.views`` authorizes, so a session
behind the shared gate without a persona is sent to the persona chooser, and an
account without a business role gets a workbook with no Teemad in it
(docs/adr/0181). Everything the file says is decided in
``app.reporting.legacy_workbook``.

The file served writes one ``EXPORT_GENERATED`` row with the number of Teemad
in it and nothing it says (`app.audit.access`, B-01).
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from app.audit.access import record_export
from app.core.decorators import viewer_for
from app.core.xlsx_safety import workbook_response
from app.reporting.legacy_workbook import (
    legacy_filename,
    legacy_population,
    legacy_register_workbook,
)


@login_required
@require_GET
def export_legacy_xlsx(request: HttpRequest) -> HttpResponse:
    """The register this reader may see, in the 2026 Excel layout."""
    today = timezone.localdate()
    reader = viewer_for(request)
    workbook = legacy_register_workbook(reader, today=today)
    response = workbook_response(workbook, legacy_filename(today))
    # One row per Teema of the population the workbook is built from
    # (`legacy_population`, one sheet row each across the year sheets).
    record_export(
        request,
        export="reporting.legacy_register",
        file_format="xlsx",
        rows=legacy_population(reader).count(),
        params=None,
    )
    return response
