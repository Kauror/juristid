"""Monitooring → Riigikogu, and the decisions on an invitation (ADR 0225, ADR 0226).

The page is Monitooring's (the department head's, or everyone's once the head
opened it). The decisions are also taken from a Teema's «Registritest» tab by
anyone who may write business content and see that Teema (``tagasi=teema``).
Every decision is a POST through a named service; a GET changes nothing.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from app.core.decorators import business_write_required
from app.core.errors import DomainError
from app.register_watch import candidate_notices
from app.register_watch.candidate_views import NOT_HERE, monitooring_context
from app.riigikogu import bills, cycle, invitations, responses, selectors
from app.riigikogu.enums import ResponseCheck
from app.riigikogu.models import Invitation

NO_TEEMA = "Sellist avatud teemat ei leitud."


@login_required
@require_http_methods(["GET"])
def monitooring(request: HttpRequest) -> HttpResponse:
    if not candidate_notices.may_see(request.user):
        raise Http404(NOT_HERE)
    everything = request.GET.get("komisjonid") == "koik"
    page = selectors.monitooring(request.user, everything=everything)
    return render(
        request,
        "riigikogu/monitooring.html",
        {
            **monitooring_context(request, "riigikogu"),
            "page": page,
            "everything": everything,
            "may_decide": candidate_notices.may_decide(request.user),
            "watch_on": cycle.enabled(),
            "health": [h for h in cycle.health() if h.state not in ("ok", "disabled")],
            "response_choices": [(c.value, c.label) for c in responses.PERSON_CHOICES],
            "today": timezone.localdate(),
        },
    )


def _back(request: HttpRequest, invitation: Invitation | None = None) -> HttpResponseRedirect:
    if request.POST.get("tagasi") == "teema":
        pk = request.POST.get("teema_id")
        if pk:
            return HttpResponseRedirect(reverse("matters:matter_registers", kwargs={"pk": pk}))
    return HttpResponseRedirect(reverse("register_watch:riigikogu"))


def _teema(request: HttpRequest, raw: str) -> Any:
    """An open Teema the person may see, by its uuid or its «2026_225» reference."""
    from app.matters.models import Matter

    visible = Matter.objects.visible_to(request.user).filter(is_open=True, deleted_at__isnull=True)
    raw = (raw or "").strip()
    year, _, number = raw.partition("_")
    if year.isdigit() and number.isdigit():
        return visible.filter(reference_year=int(year), reference_number=int(number)).first()
    try:
        return visible.filter(pk=raw).first()
    except (ValueError, TypeError):
        return None
    except Exception:  # an unparseable uuid
        return None


def _may_act(request: HttpRequest, invitation: Invitation, matter: Any | None) -> bool:
    """Monitooring's deciders, or anyone who sees a Teema the invitation's bill is tied to."""
    if candidate_notices.may_decide(request.user):
        return True
    bill = invitation.document.bill
    if matter is None or bill is None:
        return False
    return bills.teemad_of(bill).filter(pk=matter.pk).exists()


def _day(raw: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat((raw or "").strip())
    except ValueError:
        return None


def _time(raw: str) -> dt.time | None:
    raw = (raw or "").strip().replace(".", ":")
    if not raw:
        return None
    try:
        return dt.time.fromisoformat(raw)
    except ValueError:
        return None


@login_required
@business_write_required
@require_http_methods(["POST"])
def decide(request: HttpRequest, pk: Any) -> HttpResponse:
    """One decision on one invitation: date, link, reminder, nothing, confirm, answer."""
    invitation = (
        Invitation.objects.select_related("document", "document__bill").filter(pk=pk).first()
    )
    if invitation is None:
        raise Http404(NOT_HERE)
    matter = _teema(request, str(request.POST.get("teema_id") or request.POST.get("teema") or ""))
    if not _may_act(request, invitation, matter):
        raise Http404(NOT_HERE)
    action = request.POST.get("otsus")
    try:
        if action == "kuupaev":
            if matter is None:
                raise DomainError(NO_TEEMA)
            invitations.add_as_date(invitation, matter, actor=request.user)
            messages.success(request, f"Tähtaeg lisatud teemale „{matter.title}”.")
        elif action == "seo":
            from app.intelligence.models import MatterImportantDate

            if matter is None:
                raise DomainError(NO_TEEMA)
            record = (
                MatterImportantDate.objects.visible_to(request.user)
                .filter(matter=matter, pk=request.POST.get("tahtaeg"))
                .first()
            )
            if record is None:
                raise DomainError("Sellist tähtaega teemal ei ole.")
            invitations.link_to_date(invitation, record, actor=request.user)
            messages.success(request, "Kutse seotud olemasoleva tähtajaga.")
        elif action == "meeldetuletus":
            day = _day(str(request.POST.get("paev", "")))
            if matter is None or day is None:
                raise DomainError("Vali teema ja päev.")
            invitations.add_reminder(
                invitation,
                matter,
                actor=request.user,
                remind_on=day,
                note=str(request.POST.get("markus", "")),
            )
            messages.success(request, "Meeldetuletus lisatud.")
        elif action == "pole_vaja":
            invitations.no_action(invitation, actor=request.user)
        elif action == "kinnita":
            day = _day(str(request.POST.get("paev", "")))
            if day is None:
                raise DomainError("Kirjuta kuupäev, mille kutse lause annab.")
            invitations.confirm_date(
                invitation,
                actor=request.user,
                day=day,
                time=_time(str(request.POST.get("kell", ""))),
            )
            messages.success(request, "Tähtaeg kinnitatud.")
        elif action == "vastus":
            answer = str(request.POST.get("vastus", ""))
            responses.settle(
                invitation,
                actor=request.user,
                answer=answer,
                note=str(request.POST.get("markus", "")),
            )
            messages.success(request, f"Märgitud: {ResponseCheck(answer).label}.")
        else:
            raise DomainError("Tundmatu otsus.")
    except DomainError as refusal:
        messages.error(request, str(refusal))
    return _back(request, invitation)


@login_required
@business_write_required
@require_http_methods(["POST"])
def link_bill(request: HttpRequest, pk: Any) -> HttpResponse:
    """«Seo Riigikogu eelnõu» on a Teema: by number («1082 SE»), confirmed by a person."""
    from app.matters.models import Matter

    matter = Matter.objects.visible_to(request.user).filter(pk=pk, deleted_at__isnull=True).first()
    if matter is None:
        raise Http404(NOT_HERE)
    back = HttpResponseRedirect(reverse("matters:matter_registers", kwargs={"pk": matter.pk}))
    found = bills.mark_of(str(request.POST.get("eelnou", "")))
    if found is None:
        messages.error(request, "Kirjuta eelnõu number kujul «1082 SE».")
        return back
    try:
        bill = bills.find(found[0], found[1])
        if bill is None:
            raise DomainError(bills.NOT_A_BILL)
        bills.link(matter, bill, actor=request.user)
        messages.success(request, f"Riigikogu eelnõu {bill.label} seotud teemaga.")
    except (DomainError, bills.client.FetchError) as refusal:
        messages.error(request, str(refusal))
    return back


@login_required
@business_write_required
@require_http_methods(["POST"])
def unlink_bill(request: HttpRequest, pk: Any) -> HttpResponse:
    from app.register_watch.models import WatchSource, WatchTarget

    target = (
        WatchTarget.objects.filter(pk=pk, source=WatchSource.RIIGIKOGU, active=True)
        .select_related("matter")
        .first()
    )
    if target is None:
        raise Http404(NOT_HERE)
    from app.matters.models import Matter

    if not Matter.objects.visible_to(request.user).filter(pk=target.matter_id).exists():
        raise Http404(NOT_HERE)
    bills.unlink(target, actor=request.user)
    messages.success(request, "Eelnõu jälgimine sellel teemal lõpetatud.")
    return HttpResponseRedirect(
        reverse("matters:matter_registers", kwargs={"pk": target.matter_id})
    )


@login_required
@business_write_required
@require_http_methods(["POST"])
def propose_victory(request: HttpRequest, pk: Any, index: int) -> HttpResponse:
    """«Lisa töövõidu kandidaadiks»: a person proposes; the existing service records a
    candidate, never a confirmed win (docs/adr/0227 §6)."""
    from app.intelligence.services import add_work_victory_candidate
    from app.riigikogu.enums import AnalysisStatus, AnalysisType
    from app.riigikogu.models import Analysis

    analysis = Analysis.objects.filter(
        pk=pk, analysis_type=AnalysisType.PROPOSAL_OUTCOMES, status=AnalysisStatus.DONE
    ).first()
    matter = _teema(request, str(request.POST.get("teema_id", "")))
    if analysis is None or matter is None or analysis.bill is None:
        raise Http404(NOT_HERE)
    if not bills.teemad_of(analysis.bill).filter(pk=matter.pk).exists():
        raise Http404(NOT_HERE)
    rows = analysis.result.get("proposals") or []
    if not 0 <= index < len(rows):
        raise Http404(NOT_HERE)
    row = rows[index]
    level = {
        "DIRECT": "otsene tõend (juhtivkomisjoni muudatusettepanekute loetelu)",
        "SHARED": "ühine panus",
        "ALIGNMENT": "sisuline kokkulangevus, põhjuslikkust ei näita",
    }.get(str(row.get("evidence_level")), "tõendeid napib")
    try:
        add_work_victory_candidate(
            matter=matter,
            title=str(row.get("proposal") or "Koja ettepanek")[:500],
            detail=f"{row.get('quote', '')}\n\nTõendi tugevus: {level}."
            + (" See on mudeli simuleeritud näidis." if analysis.mocked else ""),
            source_url=analysis.bill.url,
            actor=request.user,
            source_text=str(row.get("quote", ""))[:2000],
        )
        messages.success(request, "Töövõidu kandidaat lisatud. Kinnitab inimene teema lehel.")
    except DomainError as refusal:
        messages.error(request, str(refusal))
    return HttpResponseRedirect(reverse("matters:matter_registers", kwargs={"pk": matter.pk}))
