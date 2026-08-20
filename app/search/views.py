"""Global search.

The view stays thin: it asks ``app.search.services`` and renders what comes
back. The count is taken from the database rather than from the length of the
rendered page, because those are different numbers as soon as the result set is
longer than one screen — and the count is the thing a lawyer uses to decide
whether their search was any good.

Stage 2B widens what can come back: a result may now be a Matter, an entry, a
sent opinion, or page 14 of an annex. Each row carries where it came from and
links as close to that as a server-rendered page can get.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from app.matters.selectors import current_action_of
from app.search.models import SearchSourceKind
from app.search.services import MATCH_REFERENCE, MAX_RESULTS, result_count, search


@login_required
def search_view(request: HttpRequest) -> HttpResponse:
    query = (request.GET.get("q") or "").strip()
    results = search(query=query, user=request.user) if query else []
    # Counted with the same authorized queryset that produced the rows, so the
    # number can never describe a result set the user is not allowed to have.
    total = result_count(query=query, user=request.user) if query else 0

    # A reference that resolves to exactly one Matter is a navigation, not a
    # search: typing 2026_184 means "open that file".
    if total == 1 and len(results) == 1 and results[0].match_kind == MATCH_REFERENCE:
        return redirect("matters:matter_detail", pk=results[0].matter.pk)

    rows = [
        {
            "result": result,
            "action": current_action_of(result.matter) if result.is_matter else None,
            "target": _target_url(result),
        }
        for result in results
    ]

    return render(
        request,
        "search/results.html",
        {
            "query": query,
            "rows": rows,
            "result_count": total,
            "shown_count": len(rows),
            "is_truncated": total > len(rows),
            "result_limit": MAX_RESULTS,
            "nav_active": "otsing",
        },
    )


def _target_url(result: object) -> str:
    """Where clicking this result goes — as close to the source as practical.

    Server-rendered URLs and anchors, not a client-side router: the timeline is
    already one page with one anchor per entry, and a document already has a
    detail page. Building a navigation layer to get a few hundred pixels closer
    would be a large amount of machinery for a scroll position
    (Stage-2B brief 77).
    """
    kind = result.source_kind  # type: ignore[attr-defined]
    matter_url = reverse("matters:matter_detail", kwargs={"pk": result.matter.pk})  # type: ignore[attr-defined]

    if kind == SearchSourceKind.DOCUMENT_FRAGMENT and result.document_id:  # type: ignore[attr-defined]
        return reverse("documents:document_detail", kwargs={"pk": result.document_id})  # type: ignore[attr-defined]
    if kind == SearchSourceKind.ENTRY and result.entry_id:  # type: ignore[attr-defined]
        return f"{matter_url}#sissekanne-{result.entry_id}"  # type: ignore[attr-defined]
    if kind == SearchSourceKind.LEGACY_SOURCE_PAGE and result.source_page_id:  # type: ignore[attr-defined]
        return reverse(
            "legacy_import:source_page",
            kwargs={"pk": result.source_page_id},  # type: ignore[attr-defined]
        )
    if kind == SearchSourceKind.SUBMISSION and result.submission_id:  # type: ignore[attr-defined]
        position = reverse("matters:matter_position", kwargs={"pk": result.matter.pk})  # type: ignore[attr-defined]
        return f"{position}#arvamus-{result.submission_id}"  # type: ignore[attr-defined]
    return matter_url
