"""«Uued dokumendid registrites»: what the watch found, for a lawyer to add or skip (ADR 0217)."""

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
from app.register_watch.models import Relevance, WatchSource

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


def _teema(request: HttpRequest, reference: str) -> Any:
    from app.matters.models import Matter

    year, _, number = reference.partition("_")
    if not (year.isdigit() and number.isdigit()):
        return None
    return (
        Matter.objects.visible_to(request.user)
        .filter(reference_year=int(year), reference_number=int(number), deleted_at__isnull=True)
        .first()
    )


def watch_line(user: Any, matter: Any) -> dict[str, Any] | None:
    """For the Teema page: how many documents wait, when any do.

    The way to the watch itself (and its search words) is the rail's
    «Menetluse link» card, which needs no query.
    """
    if not matter.is_open:
        return None
    waiting = (
        watch.pending_for(user).filter(matter=matter).exclude(relevance=Relevance.UNRELATED).count()
    )
    return {"waiting": waiting} if waiting else None


def _home_source(teema: Any) -> str:
    """The register the phrase form starts on: the one searched by words, else the ministry's."""
    if teema is None:
        return ""
    from app.register_watch import phrases

    active = teema.watch_targets.filter(origin__in=watch.PHRASE_ORIGINS, active=True).first()
    if active is not None:
        return str(active.source)
    home = phrases.ministry_source(teema)
    return str(home) if home else ""


@login_required
def review(request: HttpRequest) -> HttpResponse:
    chosen = _filters(request.GET)
    teema = _teema(request, chosen["teema"]) if chosen["teema"] else None
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
            "teema": teema,
            "watching": (
                list(teema.watch_targets.filter(active=True).order_by("source")) if teema else []
            ),
            "home_source": _home_source(teema),
            "phrase_sources": [
                (value, label) for value, label in WatchSource.choices if value in watch.SEARCHED
            ],
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
        decision = request.POST.get("otsus")
        if decision == "lisa":
            watch.add_to_teema(finding, actor=request.user)
        elif decision == "link":
            watch.add_link(finding, actor=request.user)
        else:
            watch.dismiss(finding, actor=request.user)
    except DomainError as refusal:
        messages.error(request, str(refusal))
    return _back(request)


@login_required
@require_http_methods(["POST"])
@business_write_required
def phrase(request: HttpRequest, pk: Any) -> HttpResponse:
    """«Otsingusõnad»: the words a Teema is searched by in one ministry register."""
    from app.matters.models import Matter

    matter = (
        Matter.objects.visible_to(request.user)
        .filter(pk=pk, is_open=True, deleted_at__isnull=True)
        .first()
    )
    if matter is None:
        raise Http404("Teemat ei leitud.")
    try:
        watch.set_phrase(
            matter,
            source=str(request.POST.get("allikas", "") or ""),
            text=str(request.POST.get("sonad", "") or ""),
        )
    except DomainError as refusal:
        messages.error(request, str(refusal))
    query = urlencode({"teema": matter.display_reference})
    return HttpResponseRedirect(f"{reverse('register_watch:review')}?{query}")
