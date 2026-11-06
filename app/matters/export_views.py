"""Teemad's two Excel downloads (app/matters/register_export.py).

Thin, and out of `app/matters/views.py` on purpose: the register view decides
nothing here. Both are reads, so both are GETs — an export changes no record —
behind the register's own gate (`@login_required`; the population starts from
`visible_to`, so an administrator without a business role gets an empty file,
exactly as they get an empty register). The response is private and never
cached (`app.core.xlsx_safety.workbook_response`).

Each file served writes one ``EXPORT_GENERATED`` row — the export's name, the
row count and the filter names, never a title or the search text
(`app.audit.access.record_export`, B-01).
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from app.audit.access import record_export
from app.core.xlsx_safety import workbook_response
from app.matters import register_export


def _conditions(request: HttpRequest) -> list[str]:
    """The applied search and filters, in the chip words the page shows."""
    from app.matters.views import _active_filters

    words = []
    query = (request.GET.get("q") or "").strip()
    if query:
        words.append(f"Otsing: {query}")
    words += [f"{chip['label']}: {chip['value']}" for chip in _active_filters(request, request.GET)]
    return words


@login_required
@require_GET
def export_table(request: HttpRequest) -> HttpResponse:
    """«Laadi Excel tabelis kuvatud infoga» — the current address, every page of it."""
    workbook, rows = register_export.table_workbook(
        request.user, request.GET, conditions=_conditions(request)
    )
    response = workbook_response(workbook, register_export.table_filename(timezone.localdate()))
    record_export(
        request, export="matters.register_table", file_format="xlsx", rows=rows, params=request.GET
    )
    return response


@login_required
@require_GET
def export_full(request: HttpRequest) -> HttpResponse:
    """«Laadi Excel kõikide andmetega» — every visible Teema, every supported field."""
    workbook, rows = register_export.full_workbook(request.user)
    response = workbook_response(workbook, register_export.full_filename(timezone.localdate()))
    record_export(
        request, export="matters.register_full", file_format="xlsx", rows=rows, params=None
    )
    return response
