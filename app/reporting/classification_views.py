"""«Saatja ja valdkond»: confirming the proposed sender and Valdkond (ADR 0215)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from app.core.decorators import business_write_required
from app.core.errors import DomainError
from app.organisations.models import Organisation
from app.reporting import classification
from app.reporting.classification import CONFIDENCE_LABELS

PAGE_SIZE = 40


def _filters(source: Any) -> dict[str, str]:
    year = str(source.get("aasta", "") or "")
    confidence = str(source.get("kindlus", "") or "")
    return {
        "aasta": year if year.isdigit() else "",
        "kindlus": confidence if confidence in CONFIDENCE_LABELS else "",
    }


def _filtered(request: HttpRequest, chosen: dict[str, str]) -> Any:
    rows = classification.pending_for(request.user)
    if chosen["aasta"]:
        rows = rows.filter(matter__reporting_year=int(chosen["aasta"]))
    if chosen["kindlus"]:
        rows = rows.filter(confidence=chosen["kindlus"])
    return rows


def _back(request: HttpRequest) -> HttpResponseRedirect:
    query = {key: value for key, value in _filters(request.POST).items() if value}
    url = reverse("reporting:classification")
    return HttpResponseRedirect(f"{url}?{urlencode(query)}" if query else url)


@login_required
def review(request: HttpRequest) -> HttpResponse:
    from app.reporting.views import TABS

    chosen = _filters(request.GET)
    every = classification.pending_for(request.user)
    rows = _filtered(request, chosen)
    total = rows.count()
    page = list(rows[:PAGE_SIZE])
    areas = classification.offered_areas()
    by_key = {area.key: area for area in areas}
    years = sorted(
        {y for y in every.values_list("matter__reporting_year", flat=True) if y}, reverse=True
    )
    return render(
        request,
        "reporting/classification.html",
        {
            "tabs": TABS,
            "active_tab": "arvamused",
            "nav_active": "statistika",
            "filters": chosen,
            "years": years,
            "confidences": list(CONFIDENCE_LABELS.items()),
            "total": total,
            "rows": [
                {
                    "suggestion": row,
                    "areas": [by_key[key] for key in row.policy_area_keys if key in by_key],
                    "confidence": CONFIDENCE_LABELS[row.confidence],
                }
                for row in page
            ],
            "offered_areas": areas,
            "offered_senders": classification.offered_senders(),
            "back_url": reverse("reporting:dashboard"),
        },
    )


@login_required
@require_http_methods(["POST"])
@business_write_required
def decide(request: HttpRequest, pk: Any) -> HttpResponse:
    """«Kinnita» with the row's choices, or «Jäta vahele»."""
    suggestion = classification.own_pending(request.user, pk)
    if suggestion is None:
        raise Http404("Ettepanekut ei leitud.")
    try:
        if request.POST.get("otsus") == "jata":
            classification.dismiss(suggestion, actor=request.user)
        else:
            by_key = {area.key: area for area in classification.offered_areas()}
            areas = [by_key[key] for key in request.POST.getlist("valdkond") if key in by_key]
            sender_id = str(request.POST.get("saatja", "") or "")
            sender = (
                Organisation.objects.filter(
                    pk__in=[org.pk for org in classification.offered_senders()]
                )
                .filter(pk=sender_id)
                .first()
                if sender_id
                else None
            )
            classification.accept(suggestion, actor=request.user, areas=areas, sender=sender)
    except DomainError as refusal:
        messages.error(request, str(refusal))
    return _back(request)


@login_required
@require_http_methods(["POST"])
@business_write_required
def accept_page(request: HttpRequest) -> HttpResponse:
    """«Kinnita kõik»: the rows the page showed, each as proposed."""
    wanted = request.POST.getlist("ettepanek")[:PAGE_SIZE]
    rows = classification.pending_for(request.user).filter(pk__in=wanted)
    done = classification.accept_many(list(rows), actor=request.user)
    messages.success(request, f"Kinnitatud: {done}.")
    return _back(request)
