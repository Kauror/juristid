"""«Majanduspoliitika ülevaade»: the composer page, its preview, search and export.

Four routes under `/statistika/ulevaade/`, each ``@login_required`` *and*
refused with a 404 for anybody who may not read business content
(`may_read_business_content`). The other Statistika views render empty for
an administrator without a business role; this one has nothing to show such a
caller at all — not even an empty composer that names what it would compose
(docs/adr/0181, 0199 §5).

The views hold no rule. They parse the posted draft (`draft.parse`), apply the
one button that was pressed (`draft.apply_action`), resolve everything again
as the reader (`draft.compose`) and render — the whole page for a navigation,
the composer body for htmx. The export does the same and then either writes
the file or, when anything changed or is not ready, answers 409 with the
composer and the reader's draft intact.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse, QueryDict
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from app.core.authorization import may_read_business_content
from app.core.http import content_disposition
from app.core.middleware import is_htmx
from app.reporting.report_builder import docx, draft, search

PAGE_TEMPLATE = "reporting/report_builder/page.html"
BODY_TEMPLATE = "reporting/report_builder/_composer.html"
RESULTS_TEMPLATE = "reporting/report_builder/_search_results.html"

#: The same sentence for every refusal: the route is not described to a caller
#: who may not use it.
NOT_HERE = "Lehte ei leitud."


def _require_reader(request: HttpRequest) -> None:
    if not may_read_business_content(getattr(request, "user", None)):
        raise Http404(NOT_HERE)


def _context(request: HttpRequest, report: draft.ComposedReport, **extra: Any) -> dict[str, Any]:
    results = (
        search.matters(request.user, report.search, exclude={b.matter_id for b in report.blocks})
        if report.search
        else None
    )
    return {
        "report": report,
        "rows": report.table_rows(),
        "search_results": results,
        "search_query": report.search,
        "min_search": draft.MIN_SEARCH_CHARACTERS,
        "text_limit": draft.TEXT_LIMIT,
        "nav_active": "statistika",
        "preview_url": reverse("reporting:report_preview"),
        "export_url": reverse("reporting:report_export"),
        "search_url": reverse("reporting:report_search"),
        **extra,
    }


def _render(
    request: HttpRequest, report: draft.ComposedReport, *, status: int = 200, **extra: Any
) -> HttpResponse:
    template = BODY_TEMPLATE if is_htmx(request) else PAGE_TEMPLATE
    response = render(request, template, _context(request, report, **extra), status=status)
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_GET
def composer(request: HttpRequest) -> HttpResponse:
    """The page, with the proposals for the period the URL names or last month."""
    _require_reader(request)
    # Only the period is read from a URL; a draft never travels in one.
    period = QueryDict(mutable=True)
    for name in (draft.F_START, draft.F_END):
        if name in request.GET:
            period[name] = request.GET[name]
    report_draft = draft.parse(period, request.user, today=timezone.localdate())
    return _render(request, draft.compose(report_draft, request.user), dirty=False)


@login_required
@require_POST
def preview(request: HttpRequest) -> HttpResponse:
    """Apply one button to the posted draft and show the result."""
    _require_reader(request)
    report_draft = draft.parse(request.POST, request.user, today=timezone.localdate())
    review = draft.apply_action(report_draft, request.POST.get(draft.F_ACTION), request.user)
    return _render(request, draft.compose(report_draft, request.user, review=review), dirty=True)


@login_required
@require_GET
def search_topics(request: HttpRequest) -> HttpResponse:
    """«Lisa teema» results for a term. The term is the only thing in the URL.

    The draft is not posted here, so a topic already in it may be listed; adding
    it again is refused by the draft with a notice (`draft.ALREADY_ADDED`).
    """
    _require_reader(request)
    query = request.GET.get(draft.F_SEARCH, "")
    response = render(
        request,
        RESULTS_TEMPLATE,
        {
            "search_results": search.matters(request.user, query, exclude=set()),
            "search_query": query.strip(),
            "min_search": draft.MIN_SEARCH_CHARACTERS,
            "preview_url": reverse("reporting:report_preview"),
        },
    )
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def export(request: HttpRequest) -> HttpResponse:
    """«Laadi Wordi ülevaade»: reauthorize, recompute, and only then write.

    Every topic and event is resolved again as the requesting user and every
    block's fingerprint recomputed. Anything changed, unavailable or not ready
    returns the composer (409) with the reasons and the draft as posted — the
    new fingerprints in it, so the reader who has looked can export again.
    """
    _require_reader(request)
    report_draft = draft.parse(request.POST, request.user, today=timezone.localdate())
    report = draft.compose(report_draft, request.user)
    if not report_draft.composed:
        report.problems.append(draft.EXPORT_REFUSED)
        return _render(request, report, status=409, dirty=True)
    refusals = report.refusals()
    if refusals:
        return _render(request, report, status=409, dirty=True, refusals=refusals)
    response = HttpResponse(docx.render(report), content_type=docx.CONTENT_TYPE)
    response["Content-Disposition"] = content_disposition("attachment", report.filename)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
