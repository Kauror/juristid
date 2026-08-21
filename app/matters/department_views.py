"""The department-head surface.

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

from app.core.authorization import is_department_head
from app.matters.department_dashboard import build_department_work


@login_required
def department_work(request: HttpRequest) -> HttpResponse:
    """Osakonna töö — the cross-lawyer operational view.

    Gated on the ``DEPARTMENT_HEAD`` role, and on nothing else. Not a name: the
    role changes hands and a hard-coded colleague would have to be found and
    edited when it does. Not ``is_staff`` or ADMINISTRATOR: technical
    administration is not business access. Not the shared gate either —
    ``login_required`` refuses a session that passed the department password
    and chose no persona, because knowing a shared password says nothing about
    who is reading (Stage-2F brief 28, 30).

    A reader without the role gets **404**, not 403. The two are different
    disclosures: 403 confirms the page exists and that somebody else may see
    it, which is the same reasoning ``get_visible_matter`` already applies to a
    restricted Matter.

    Everything the page then shows is scoped by ``visible_to(request.user)``.
    A department head sees RESTRICTED content because the central authorization
    entitles that role, not because this view decided it — so the entitlement
    is described in exactly one place and changing it there changes it here.
    """
    if not is_department_head(request.user):
        raise Http404("This surface is for the department head.")

    return render(
        request,
        "matters/department_work.html",
        {
            "work": build_department_work(request.user),
            "today": timezone.localdate(),
            "nav_active": "osakonna_too",
        },
    )
