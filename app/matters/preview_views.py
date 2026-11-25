"""«Eelvaade»: the read-only fragment the register's preview drawer loads (UX-006).

Out of `app/matters/views.py` for the reason `export_views` is: the register view
decides nothing here, and the fragment has its own, deliberately small context
(app/matters/preview.py). A GET that changes nothing — not a notice, not a
«viewed» flag, not the last-viewed marker, which lives in the reader's own
browser session (static/js/matter-preview.js).
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from app.matters import preview


@login_required
@require_GET
def matter_preview(request: HttpRequest, pk: Any) -> HttpResponse:
    """One Teema's preview, or 404 for a Teema this reader may not open.

    404 rather than 403, as `get_visible_matter` answers: a refusal that differed
    from «not found» would tell an outsider a restricted Teema exists.
    """
    matter = preview.preview_matter(request.user, pk)
    if matter is None:
        raise Http404("Teemat ei leitud.")
    response = render(
        request,
        "matters/partials/matter_preview.html",
        preview.preview_context(matter, request.user),
    )
    # A fragment of one person's reading: never stored by a shared cache, and
    # never served from the browser's own after the file changed.
    response["Cache-Control"] = "private, no-store"
    return response
