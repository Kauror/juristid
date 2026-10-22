"""The department surface.

One view, in its own module rather than in ``views.py``. Partly because it is a
separate surface with a separate audience, and partly for a practical reason:
``views.py`` is where the register's search and filters live, and two branches
editing the same thousand-line file for unrelated reasons produces conflicts
that are resolved under time pressure — which is how a role gate quietly loses
a line (Stage-2F brief 31).
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from app.core.authorization import may_view_department_management
from app.core.decorators import viewer_for
from app.matters import overview as overview_module
from app.matters.department import build_department
from app.matters.department_changes import build_changes
from app.matters.department_dashboard import PERIOD_END_PARAM, PERIOD_START_PARAM, TEAM_COLUMNS


@login_required
def department(request: HttpRequest) -> HttpResponse:
    """Osakond — where the department stands, in two scopes behind one shell.

    The two pages this replaced. ``/ulevaade/`` answered "where is the
    department losing time" for everybody and ``/osakonna-too/`` answered "what
    is the team doing, what is ahead, what is done" for the head; between them
    they asked one question and printed several of the same numbers twice. Both
    old addresses now redirect here permanently (docs/adr/0049).

    **Read access needs a persona since docs/adr/0181.** This was the one page
    that rendered straight from the shared door, before anybody said who was
    reading, as a department scope of NORMAL records. Under one internal read
    policy that is not an authorized internal user, so ``login_required`` sends
    such a session to the persona chooser, and every persona then reads the same
    department.

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
                # Since docs/adr/0145 the `department.view_management`
                # capability, whose default is exactly the department head.
                is_head=may_view_department_management(request.user),
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


@login_required
def department_changes(request: HttpRequest) -> HttpResponse:
    """Muudatused — which states moved in the department's work over a window.

    **The department head's, by the same rule as Meeskond and Tehtud**
    (docs/adr/0159 §4): `may_view_department_management(request.user)`, the
    authenticated identity, never the viewer the shared gate lends the rest of
    Osakond. Those two are sections and are simply not built for anybody else;
    this is a page of its own, so for anybody else it does not exist — a 404,
    the answer the manager-only `/osakonna-too/` gave and a colleague's desk
    still gives (`person_work`). Nothing is computed before the check.

    What the page shows is still `visible_to(viewer)`: the role decides that it
    opens, not what it may count.
    """
    if not may_view_department_management(request.user):
        raise Http404("Muudatused on osakonnajuhataja vaade.")
    today = timezone.localdate()
    return render(
        request,
        "matters/department_changes.html",
        {
            "page": build_changes(viewer_for(request), request.GET, today=today),
            # What the reader typed, redisplayed as typed: a refused range
            # shows the words that were refused, not the fallback's dates.
            "typed_start": request.GET.get(PERIOD_START_PARAM, ""),
            "typed_end": request.GET.get(PERIOD_END_PARAM, ""),
            "today": today,
            "nav_active": "osakond",
        },
    )
