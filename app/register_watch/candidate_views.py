"""«Uued eelnõud»: drafts in the registers that are not a Teema yet (ADR 0218).

The department head's page. Each candidate is a proposal: «Lisa teemaks» opens
Uus teema filled from it, «Seo olemasolevaga» writes its register addresses on
a Teema that already exists, «Jäta kõrvale» only records the decision.
"""

from __future__ import annotations

from typing import Any, cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from app.core.decorators import business_write_required
from app.core.errors import DomainError
from app.register_watch import candidate_notices, candidates
from app.register_watch.models import GovernmentAgenda, Strength, SweepRun

NOT_HERE = "Lehte ei leitud."
NO_TEEMA = "Sellist avatud teemat ei leitud."


def _visible_open_teema(user: Any, reference: str) -> Any:
    from app.matters.models import Matter

    year, _, number = (reference or "").strip().partition("_")
    if not (year.isdigit() and number.isdigit()):
        return None
    return (
        Matter.objects.visible_to(user)
        .filter(
            is_open=True,
            deleted_at__isnull=True,
            reference_year=int(year),
            reference_number=int(number),
        )
        .first()
    )


#: Monitooring's sections, in the order of its tabs (ADR 0219).
SECTIONS = (
    ("eelnoud", "Uued eelnõud", "register_watch:candidates"),
    ("valitsus", "Valitsuse info", "register_watch:agendas"),
    ("registrid", "Registrite dokumendid", "register_watch:review"),
)


def monitooring_context(request: HttpRequest, active: str) -> dict[str, Any]:
    """What every Monitooring page shares: the tab strip and the head's audience switch."""
    return {
        "nav_active": "monitooring",
        "monitooring_sections": [(key, label, reverse(name)) for key, label, name in SECTIONS],
        "monitooring_active": active,
        "is_head": candidate_notices.is_head(request.user),
        "everyone": candidate_notices.audience().everyone,
    }


@login_required
@require_http_methods(["GET"])
def agenda_list(request: HttpRequest) -> HttpResponse:
    """«Valitsuse info»: the Government's weekly agendas, newest open (ADR 0219)."""
    if not candidate_notices.may_see(request.user):
        raise Http404(NOT_HERE)
    from app.matters.models import Matter
    from app.register_watch import agenda

    agendas = list(GovernmentAgenda.objects.prefetch_related("items")[:12])
    related_ids = {
        item.related_matter_id for a in agendas for item in a.items.all() if item.related_matter_id
    }
    visible = {m.pk: m for m in Matter.objects.visible_to(request.user).filter(pk__in=related_ids)}
    weeks = []
    for a in agendas:
        groups: dict[str, list[Any]] = {"shown": [], "check": [], "rest": []}
        for item in a.items.all():
            # A Teema the reader may not see is not named to them.
            cast(Any, item).related_shown = visible.get(item.related_matter_id)
            if item.strength in agenda.SHOWN:
                groups["shown"].append(item)
            elif item.strength in agenda.CHECK:
                groups["check"].append(item)
            else:
                groups["rest"].append(item)
        groups["shown"].sort(key=lambda i: (i.strength != Strength.STRONG, i.number))
        weeks.append({"agenda": a, **groups, "total": len(a.items.all())})
    return render(
        request,
        "register_watch/agendas.html",
        {
            **monitooring_context(request, "valitsus"),
            "weeks": weeks,
            "missing": agenda.missing_this_week(),
            "watch_on": agenda.enabled(),
        },
    )


@login_required
@require_http_methods(["GET"])
def candidate_list(request: HttpRequest) -> HttpResponse:
    if not candidate_notices.may_see(request.user):
        raise Http404(NOT_HERE)
    from app.matters.models import Matter

    rows = list(candidates.waiting().prefetch_related("items").select_related("related_matter"))
    visible = set(
        Matter.objects.visible_to(request.user)
        .filter(pk__in=[row.related_matter_id for row in rows if row.related_matter_id])
        .values_list("pk", flat=True)
    )
    shown: list[Any] = []
    folded: list[Any] = []
    nameless: list[Any] = []
    for row in rows:
        # A Teema the reader may not see is not named to them.
        cast(Any, row).related_shown = (
            row.related_matter if row.related_matter_id in visible else None
        )
        if not row.match_key:
            nameless.append(row)
        elif row.strength in candidates.FOLDED:
            folded.append(row)
        else:
            shown.append(row)
    rank = {Strength.STRONG: 0, Strength.POSSIBLE: 1, Strength.UNJUDGED: 2}
    shown.sort(key=lambda row: rank.get(row.strength, 3))
    return render(
        request,
        "register_watch/candidates.html",
        {
            **monitooring_context(request, "eelnoud"),
            "shown": shown,
            "folded": folded,
            "nameless": nameless,
            "may_decide": candidate_notices.may_decide(request.user),
            "last_run": SweepRun.objects.order_by("-started_at").first(),
            "sweep_on": candidates.enabled(),
        },
    )


def _back() -> HttpResponseRedirect:
    return HttpResponseRedirect(reverse("register_watch:candidates"))


@login_required
@require_http_methods(["POST"])
@business_write_required
def decide(request: HttpRequest, pk: Any) -> HttpResponse:
    """«Lisa teemaks», «Seo olemasolevaga» or «Jäta kõrvale»."""
    if not candidate_notices.may_decide(request.user):
        raise Http404(NOT_HERE)
    candidate = candidates.waiting().filter(pk=pk).first()
    if candidate is None:
        messages.error(request, candidates.ALREADY_DECIDED)
        return _back()
    decision = request.POST.get("otsus")
    if decision == "lisa":
        return HttpResponseRedirect(f"{reverse('matters:matter_create')}?kandidaat={candidate.pk}")
    try:
        if decision == "seo":
            matter = _visible_open_teema(request.user, str(request.POST.get("teema", "")))
            if matter is None:
                messages.error(request, NO_TEEMA)
                return _back()
            candidates.attach(candidate, matter, actor=request.user)
            messages.success(request, f"Seotud teemaga „{matter.title}”.")
        else:
            candidates.dismiss(candidate, actor=request.user)
    except DomainError as refusal:
        messages.error(request, str(refusal))
    return _back()


@login_required
@require_http_methods(["POST"])
@business_write_required
def audience(request: HttpRequest) -> HttpResponse:
    """The head opens Monitooring to every reader, or keeps it to the head (ADR 0219)."""
    if not candidate_notices.is_head(request.user):
        raise Http404(NOT_HERE)
    candidate_notices.set_everyone(request.POST.get("koigile") == "1")
    # Back to the section it was pressed on, one of Monitooring's own (never a free URL).
    names = {key: name for key, _label, name in SECTIONS}
    return HttpResponseRedirect(
        reverse(names.get(str(request.POST.get("tagasi", "")), "register_watch:candidates"))
    )
