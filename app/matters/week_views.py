"""The records behind the week overview — a temporary, URL-held preset (r97 §19–20).

``/minu-asjad/nadal/?naitaja=tehtud&nadal=2026-11-23`` lists exactly the rows one
figure of «Selle nädala ülevaade» counted, because it calls the **same resolver**
(`week_overview.records`) the figure does. The whole state is the query string:
a refresh, Back and a pasted link show the same page, and «Eemalda eelseadistus»
is a plain link to the page the preset came from. Nothing here reads or writes a
saved view, a saved filter or a default column.

Two scopes, both enforced here at the route and by the queries underneath, not by
what a template shows:

* the default is the signed-in person's own records. There is no person in the
  URL, so nobody can ask for a colleague's;
* ``?ulatus=osakond`` is the department's, and answers 403 unless the reader may
  view department management (`may_view_department_management`, the head).

Every row is read through ``visible_to`` for the reader, so a shared link shows a
colleague only what they may open.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from app.core.authorization import may_view_department_management
from app.core.decorators import viewer_for
from app.matters import stat_periods
from app.matters import week_overview as wo

#: Figures whose history is the person's own, and so does not exist for «Osakond»
#: unless the head asks for it.
WEEKLY = (*wo.HISTORY_KINDS, wo.OWED, wo.LATE, *wo.COMMENCEMENT_KINDS, wo.OUTSTANDING)
TITLES = wo.RECORD_TITLES


def _day(raw: str | None) -> date | None:
    try:
        return date.fromisoformat(raw or "")
    except ValueError:
        return None


def build_records(request: HttpRequest, kind: str, today: date) -> tuple[list[wo.Rec], str, bool]:
    """The rows of one preset, the period line, and whether it is the department's."""
    department = request.GET.get(wo.SCOPE_PARAM) == wo.DEPARTMENT_VALUE
    viewer: Any = viewer_for(request)
    if department:
        if not may_view_department_management(request.user):
            raise PermissionDenied
        ctx = wo.make_ctx(viewer, wo.DEPARTMENT, None, today)
    else:
        ctx = wo.make_ctx(request.user, wo.SELF, request.user, today)
    if kind in WEEKLY or request.GET.get("nadal"):
        start = wo.week_start_of(request.GET.get("nadal"), today)
        end = start + timedelta(days=6)
        if kind in (wo.OWED, wo.LATE, *wo.COMMENCEMENT_KINDS, wo.OUTSTANDING):
            # Present-state figures are read for the current week only.
            start, end = ctx.week.start, ctx.week.end
        period = f"{start:%d.%m.%Y}–{end:%d.%m.%Y}"
    else:
        start = max(
            _day(request.GET.get("alates")) or wo.preview_window(today).start,
            stat_periods.FIRST_DAY,
        )
        end = today
        period = f"alates {start:%d.%m.%Y}"
    if ctx.data is not None and start < ctx.last.start:
        # The series loaded for the overview cover two weeks; a longer or older
        # window reads its own.
        from app.matters.my_statistics import load_series

        ctx.data = load_series(request.user, today, start, today)
    return wo.records(ctx, kind, stat_periods.Span(start, end)), period, department


@login_required
@require_GET
def my_week_records(request: HttpRequest) -> HttpResponse:
    today = timezone.localdate()
    kind = request.GET.get("naitaja", "")
    if kind not in TITLES:
        kind = wo.SENT
    rows, period, department = build_records(request, kind, today)
    back = reverse("matters:department") if department else reverse("matters:my_work")
    return render(
        request,
        "matters/week_records.html",
        {
            "kind": kind,
            "title": TITLES[kind],
            "rows": rows,
            "period": period,
            "is_action": kind in (*wo.HISTORY_KINDS, wo.OUTSTANDING),
            "department": department,
            "back_url": back,
            "nav_active": "osakond" if department else "minu_asjad",
        },
    )
