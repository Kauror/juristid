"""Global search.

The view stays thin: it asks ``app.search.services`` and renders what comes
back. The count is taken from the database rather than from the length of the
rendered page, because those are different numbers as soon as the result set is
longer than one screen — and the count is the thing a lawyer uses to decide
whether their search was any good.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from app.matters.selectors import current_action_of
from app.search.services import MATCH_REFERENCE, MAX_RESULTS, result_count, search_matters


@login_required
def search(request: HttpRequest) -> HttpResponse:
    query = (request.GET.get("q") or "").strip()
    results = search_matters(query=query, user=request.user) if query else []
    # Counted with the same authorized queryset that produced the rows, so the
    # number can never describe a result set the user is not allowed to have.
    total = result_count(query=query, user=request.user) if query else 0

    # A reference that resolves to exactly one Matter is a navigation, not a
    # search: typing 2026_184 means "open that file".
    if total == 1 and len(results) == 1 and results[0].match_kind == MATCH_REFERENCE:
        return redirect("matters:matter_detail", pk=results[0].matter.pk)

    rows = [{"result": result, "action": current_action_of(result.matter)} for result in results]

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
