"""Global search.

The view is intentionally thin: it hands the query to
``app.search.services.search_matters`` and renders what comes back. Stage 2
replaces that implementation with the ``SearchDocument`` projection without this
file, the template or the navigation changing.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from app.matters.selectors import current_action_of
from app.search.services import search_matters


@login_required
def search(request: HttpRequest) -> HttpResponse:
    query = (request.GET.get("q") or "").strip()
    results = search_matters(query=query, user=request.user) if query else []

    # A reference that resolves to exactly one Matter is a navigation, not a
    # search: typing 2026_184 means "open that file".
    if len(results) == 1 and results[0].match_kind == "reference":
        return redirect("matters:matter_detail", pk=results[0].matter.pk)

    rows = [{"result": result, "action": current_action_of(result.matter)} for result in results]

    return render(
        request,
        "search/results.html",
        {
            "query": query,
            "rows": rows,
            "result_count": len(results),
            "nav_active": "otsing",
        },
    )
