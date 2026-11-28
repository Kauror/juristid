"""The records behind the week overview — a temporary, URL-held preset (r97 §19–20).

``/minu-asjad/nadal/?naitaja=tehtud&nadal=2026-11-23`` lists exactly the rows one
figure of «Selle nädala ülevaade» counted. The whole state is the query string, so
a refresh, Back and a pasted link show the same page; «Eemalda eelseadistus» is a
plain link to the ordinary Minu asjad, and nothing here reads or writes a saved
view, a saved filter or a default column — a preset is never persisted.

Self only, like Minu statistika: the route takes no person, and every row is read
through ``visible_to(request.user)``, so a shared URL shows a colleague only what
they may open.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from app.matters import stat_periods
from app.matters.models import Matter
from app.matters.week_overview import (
    ACTION_LABELS,
    action_records,
    week_start_of,
)

SENT, NEW = "saadetud-arvamused", "uued-teemad"
TITLES = {**ACTION_LABELS, SENT: "Saadetud arvamused", NEW: "Uued teemad"}


@dataclass(frozen=True)
class Row:
    reference: str
    title: str
    url: str
    day: date | None
    what: str = ""
    note: str = ""


def _ref(matter: Matter) -> str:
    year, number = matter.reference_year, matter.reference_number
    return f"{year}_{number}" if year and number else ""


def _day(raw: str | None) -> date | None:
    try:
        return date.fromisoformat(raw or "")
    except ValueError:
        return None


def build_rows(user: Any, kind: str, params: Any, today: date) -> tuple[list[Row], str]:
    """The rows of one preset and the period line that says which days they cover."""
    if kind in ACTION_LABELS:
        start = week_start_of(params.get("nadal"), today)
        end = start + timedelta(days=6)
        rows = [
            Row(
                _ref(action.matter),
                action.matter.title,
                reverse("matters:matter_detail", kwargs={"pk": action.matter_id}),
                day,
                what=action.text,
                note=ACTION_LABELS[outcome],
            )
            for action, outcome, day in action_records(user, user, kind, start, end)
        ]
        return rows, f"{start:%d.%m.%Y}–{end:%d.%m.%Y}"
    since = max(_day(params.get("alates")) or date(today.year, 1, 1), stat_periods.FIRST_DAY)
    if kind == SENT:
        from app.reporting import opinions

        facts = [
            f
            for f in opinions.opinion_facts(user)
            if f.sent_on >= since
            and (str(f.actor_id) == str(user.pk) if f.actor_id else f.matter.owner_id == user.pk)
        ]
        rows = [
            Row(
                f.matter.reference,
                f.matter.title,
                reverse("matters:matter_detail", kwargs={"pk": f.matter.matter_id}),
                f.sent_on,
                note="Lovable'i teatatud" if f.source == opinions.SOURCE_IMPORTED else "",
            )
            for f in sorted(facts, key=lambda f: f.sent_on, reverse=True)
        ]
        return rows, f"alates {since:%d.%m.%Y}"
    if kind == NEW:
        found = (
            Matter.objects.visible_to(user)
            .real_data()
            .filter(owner=user, received_date__gte=since)
            .order_by("-received_date", "-created_at")
        )
        rows = [
            Row(
                _ref(m),
                m.title,
                reverse("matters:matter_detail", kwargs={"pk": m.pk}),
                m.received_date,
            )
            for m in found
        ]
        return rows, f"saabunud alates {since:%d.%m.%Y}"
    return [], ""


@login_required
@require_GET
def my_week_records(request: HttpRequest) -> HttpResponse:
    today = timezone.localdate()
    kind = request.GET.get("naitaja", "")
    if kind not in TITLES:
        kind = SENT
    rows, period = build_rows(request.user, kind, request.GET, today)
    return render(
        request,
        "matters/week_records.html",
        {
            "kind": kind,
            "title": TITLES[kind],
            "rows": rows,
            "period": period,
            "is_action": kind in ACTION_LABELS,
            "nav_active": "minu_asjad",
        },
    )
