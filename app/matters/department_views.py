"""The department surface.

One view, in its own module rather than in ``views.py``. Partly because it is a
separate surface with a separate audience, and partly for a practical reason:
``views.py`` is where the register's search and filters live, and two branches
editing the same thousand-line file for unrelated reasons produces conflicts
that are resolved under time pressure — which is how a role gate quietly loses
a line (Stage-2F brief 31).
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from app.core.authorization import is_department_head
from app.core.decorators import gate_required, viewer_for
from app.matters import overview as overview_module
from app.matters.department import build_department
from app.matters.department_dashboard import TEAM_COLUMNS


@gate_required
def department(request: HttpRequest) -> HttpResponse:
    """Osakond — where the department stands, in two scopes behind one shell.

    The two pages this replaced. ``/ulevaade/`` answered "where is the
    department losing time" for everybody and ``/osakonna-too/`` answered "what
    is the team doing, what is ahead, what is done" for the head; between them
    they asked one question and printed several of the same numbers twice. Both
    old addresses now redirect here permanently (docs/adr/0049).

    **Read access is Ülevaade's, unchanged.** ``gate_required`` rather than
    ``login_required``: this is the page somebody lands on straight from the
    shared door, and it has to be worth looking at before they have said who
    they are. That reader is authorized as the department sentinel
    ``viewer_for`` resolves — NORMAL visibility, no participation — so nothing
    RESTRICTED appears because a password was typed (Stage-2D auth brief 6).

    **Two sections keep Osakonna töö's stronger boundary.** *Meeskond* and
    *Tehtud* are the department head's, by role and by nothing else: not a name,
    which changes hands; not ``is_staff`` or ADMINISTRATOR, because technical
    administration is not business access; and not the shared gate, because
    knowing a password says nothing about who is reading (Stage-2F brief 28,
    30). The check reads ``request.user`` — the *authenticated* identity —
    rather than the viewer, so a pseudo-viewer can never become a head by being
    passed to the wrong function.

    A reader without the role does not get an empty shell where those sections
    were: `build_department` is told, and never builds them. The rest of the
    page renders in full, which is the whole difference between this route and
    the 404 the manager-only page used to answer — the page is no longer
    manager-only.

    Everything that changes what is on screen comes from the URL and nowhere
    else, so the back button, a refresh and a pasted link all show the same
    page. An unrecognised value falls back rather than 500ing or rendering a
    convincing empty list.
    """
    scope = overview_module.scope_from(request.GET.get(overview_module.SCOPE_PARAM))
    sort = request.GET.get(overview_module.SORT_PARAM, overview_module.SORT_OPEN)
    show_empty_areas = request.GET.get(overview_module.SHOW_EMPTY_AREAS_PARAM) == "1"
    today = timezone.localdate()
    return render(
        request,
        "matters/department.html",
        {
            "page": build_department(
                viewer_for(request),
                is_head=is_department_head(request.user),
                scope=scope,
                today=today,
                # `request.GET`, because the Tehtud period and its row-kind
                # filter live in the URL like every other choice in this
                # product: a view somebody picked survives a refresh and can be
                # pasted to a colleague.
                params=request.GET,
                sort=sort,
                show_empty_areas=show_empty_areas,
            ),
            "scopes": overview_module.SCOPES,
            "scope": scope,
            "sort_options": overview_module.SORT_OPTIONS,
            "today": today,
            # The column headings, read from the same tuple the cells are built
            # from, so a column added later cannot arrive under the wrong
            # heading (app/matters/department_dashboard.py, `TEAM_COLUMNS`).
            "team_columns": TEAM_COLUMNS,
            "nav_active": "osakond",
        },
    )
