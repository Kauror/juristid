"""«Uued dokumendid registrites»: what the watch found, for a lawyer to add or skip (ADR 0216)."""

from __future__ import annotations

from collections import OrderedDict
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
from app.register_watch import watch
from app.register_watch.models import Relevance

WHOSE = ("minu", "koik")


def _filters(source: Any) -> dict[str, str]:
    whose = str(source.get("kelle", "") or "")
    teema = str(source.get("teema", "") or "").strip()
    year, _, number = teema.partition("_")
    return {
        "kelle": whose if whose in WHOSE else "minu",
        "teema": teema if year.isdigit() and number.isdigit() else "",
    }


def _rows(request: HttpRequest, chosen: dict[str, str]) -> Any:
    rows = watch.pending_for(request.user)
    if chosen["teema"]:
        year, _, number = chosen["teema"].partition("_")
        return rows.filter(matter__reference_year=int(year), matter__reference_number=int(number))
    if chosen["kelle"] == "minu":
        rows = rows.filter(matter__owner_id=request.user.pk)
    return rows


def pending_count(user: Any, matter: Any) -> int:
    """For the Teema page: how many findings wait on this Teema, leaving out the
    ones the model judged unrelated (they wait folded on the review page)."""
    return (
        watch.pending_for(user)
        .filter(matter=matter)
        .exclude(relevance=Relevance.UNRELATED)
        .count()
    )


@login_required
def review(request: HttpRequest) -> HttpResponse:
    chosen = _filters(request.GET)
    groups: OrderedDict[Any, dict[str, Any]] = OrderedDict()
    for finding in _rows(request, chosen):
        group = groups.setdefault(
            finding.matter_id, {"matter": finding.matter, "shown": [], "unrelated": []}
        )
        bucket = "unrelated" if finding.relevance == Relevance.UNRELATED else "shown"
        group[bucket].append(finding)
    return render(
        request,
        "register_watch/review.html",
        {
            "nav_active": "minu_asjad",
            "filters": chosen,
            "groups": list(groups.values()),
            "total": sum(len(g["shown"]) + len(g["unrelated"]) for g in groups.values()),
        },
    )


def _back(request: HttpRequest) -> HttpResponseRedirect:
    query = {key: value for key, value in _filters(request.POST).items() if value}
    return HttpResponseRedirect(f"{reverse('register_watch:review')}?{urlencode(query)}")


@login_required
@require_http_methods(["POST"])
@business_write_required
def decide(request: HttpRequest, pk: Any) -> HttpResponse:
    """«Lisa märkena» or «Jäta vahele»."""
    finding = watch.own_pending(request.user, pk)
    if finding is None:
        raise Http404("Dokumenti ei leitud.")
    try:
        if request.POST.get("otsus") == "lisa":
            watch.add_to_teema(finding, actor=request.user)
        else:
            watch.dismiss(finding, actor=request.user)
    except DomainError as refusal:
        messages.error(request, str(refusal))
    return _back(request)
