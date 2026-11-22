"""«Uued dokumendid registrites»: what the watch found, for a lawyer to add or skip (ADR 0217)."""

from __future__ import annotations

import uuid
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
    # «2026_190», or the Teema's id: the Teema page links by id, so its own
    # number stays on that page once (tests/test_identifier_free_ui.py).
    known = (year.isdigit() and number.isdigit()) or _is_uuid(teema)
    return {"kelle": whose if whose in WHOSE else "minu", "teema": teema if known else ""}


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


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

    visible = Matter.objects.visible_to(request.user).filter(deleted_at__isnull=True)
    if _is_uuid(reference):
        return visible.filter(pk=reference).first()
    year, _, number = reference.partition("_")
    if not (year.isdigit() and number.isdigit()):
        return None
    return visible.filter(reference_year=int(year), reference_number=int(number)).first()


def waiting_count(user: Any, matter: Any) -> int:
    """For the Teema page's «Registritest» tab: how many documents wait on it.

    One query, and none on a closed Teema (the watch reads open ones only).
    What the model called unrelated is not counted: it waits folded away.
    """
    if not matter.is_open:
        return 0
    # Register documents and the Government's agenda items tied to the Teema
    # (docs/adr/0219, amendment 2026-11-22), counted together in one query.
    from django.db.models import Count, IntegerField, OuterRef, Subquery
    from django.db.models.functions import Coalesce

    from app.matters.models import Matter
    from app.register_watch import agenda

    documents = (
        watch.pending_for(user)
        .filter(matter=OuterRef("pk"))
        .exclude(relevance=Relevance.UNRELATED)
        .order_by()
        .values("matter")
        .annotate(n=Count("pk"))
        .values("n")
    )
    items = (
        agenda.proposals()
        .filter(related_matter=OuterRef("pk"))
        .order_by()
        .values("related_matter")
        .annotate(n=Count("pk"))
        .values("n")
    )
    row = (
        Matter.objects.filter(pk=matter.pk)
        .annotate(
            d=Coalesce(Subquery(documents), 0, output_field=IntegerField()),
            a=Coalesce(Subquery(items), 0, output_field=IntegerField()),
        )
        .values_list("d", "a")
        .first()
    )
    return sum(row) if row else 0


#: Senders whose document registers the watch does not read (ADR 0217 §3 and
#: `docs/adr/0217`'s «deliberately not read»): their Teemad are not updated
#: automatically, and the tab says so instead of waiting for nothing.
UNREAD_REGISTERS = frozenset(
    {
        "Siseministeerium",
        "Regionaal- ja Põllumajandusministeerium",
        "Maaeluministeerium",
        "Välisministeerium",
        "Riigikantselei",
    }
)

#: The «Registritest» tab's three states besides being watched.
CLOSED_LINE = "Lõpetatud teemat registrites ei jälgita."
UNREAD_LINE = (
    "Seda teemat ei uuendata automaatselt: {sender} dokumendiregistrit ei saa masinaga "
    "lugeda. Kontrolli registrit käsitsi."
)
UNKNOWN_LINE = (
    "Seda teemat ei uuendata automaatselt: pealkirjast ega saatjast ei selgu, millises "
    "registris otsida. Vali register ja lisa otsingusõnad, siis kontrollitakse seda igal ööl."
)
WATCHED_LINE = (
    "Registreid kontrollitakse automaatselt igal ööl. Leitud dokument jõuab teemasse "
    "alles siis, kui sa selle lisad."
)


def _sender_name(matter: Any) -> str:
    """The Teema's ministry as the watch would read it: confirmed sender, else the proposed one."""
    for organisation in matter.source_organisations.all():
        return str(organisation.name)
    suggestion = getattr(matter, "classification_suggestion", None)
    sender = suggestion.sender if suggestion is not None else None
    return str(sender.name) if sender is not None else ""


def watch_state(matter: Any, watching: list[Any]) -> dict[str, str]:
    """Whether the nightly check covers this Teema, and the one line that says so.

    Watched when it already has an active source, or when the next run would
    give it one (`watch.ensure_targets`: a bill number in the title, or words
    from the title in its ministry's register). Read without the network.
    """
    from app.register_watch import phrases
    from app.register_watch.sources import riigikogu

    if not matter.is_open:
        return {"state": "closed", "line": CLOSED_LINE}
    if watching or riigikogu.bill_mark(matter.title) is not None:
        return {"state": "watched", "line": WATCHED_LINE}
    if phrases.ministry_source(matter) is not None and phrases.words(matter.title):
        return {"state": "watched", "line": WATCHED_LINE}
    sender = _sender_name(matter)
    if sender in UNREAD_REGISTERS:
        return {"state": "unread", "line": UNREAD_LINE.format(sender=sender)}
    return {"state": "unknown", "line": UNKNOWN_LINE}


def teema_panel(user: Any, matter: Any) -> dict[str, Any]:
    """The «Registritest» tab's own context: this Teema's waiting documents and its watch."""
    shown: list[Any] = []
    unrelated: list[Any] = []
    for finding in watch.pending_for(user).filter(matter=matter):
        (unrelated if finding.relevance == Relevance.UNRELATED else shown).append(finding)
    watching = list(matter.watch_targets.filter(active=True).order_by("source"))
    from app.register_watch import agenda

    return {
        # The Government's agenda items tied to this Teema, waiting for a decision.
        "agenda_proposals": agenda.teema_proposals(matter) if matter.is_open else [],
        "register_shown": shown,
        "register_unrelated": unrelated,
        "register_watching": watching,
        "register_state": watch_state(matter, watching),
        "home_source": _home_source(matter),
        "phrase_sources": [
            (value, label) for value, label in WatchSource.choices if value in watch.SEARCHED
        ],
        # The decide and phrase forms come back to this tab, not the review page.
        "filters": {"kelle": "", "teema": ""},
        "register_return": "teema",
    }


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
    """Monitooring → «Registrite dokumendid»: every Teema's waiting documents (ADR 0219).

    The head's, like the rest of Monitooring, until the head opens it to everyone. A
    lawyer decides on a Teema's documents on its own «Registritest» tab.
    """
    from app.register_watch import candidate_notices, candidate_views

    if not candidate_notices.may_see(request.user):
        raise Http404(candidate_views.NOT_HERE)
    chosen = _filters(request.GET)
    teema = _teema(request, chosen["teema"]) if chosen["teema"] else None
    if chosen["teema"]:
        chosen["teema"] = teema.display_reference if teema is not None else ""
    groups: OrderedDict[Any, dict[str, Any]] = OrderedDict()
    for finding in _rows(request, chosen):
        group = groups.setdefault(
            finding.matter_id, {"matter": finding.matter, "shown": [], "unrelated": []}
        )
        bucket = "unrelated" if finding.relevance == Relevance.UNRELATED else "shown"
        group[bucket].append(finding)
    for group in groups.values():
        group["count"] = len(group["shown"]) + len(group["unrelated"])
    return render(
        request,
        "register_watch/review.html",
        {
            **candidate_views.monitooring_context(request, "registrid"),
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


def _back(request: HttpRequest, matter_id: Any = None) -> HttpResponseRedirect:
    """Where a decision returns: the Teema's «Registritest» tab when it came from there.

    The tab is built from the finding's own Teema, never from the request, so the
    field can only choose between two of this application's own pages.
    """
    if matter_id is not None and request.POST.get("tagasi") == "teema":
        return HttpResponseRedirect(reverse("matters:matter_registers", kwargs={"pk": matter_id}))
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
    return _back(request, finding.matter_id)


@login_required
@require_http_methods(["POST"])
@business_write_required
def dismiss_all(request: HttpRequest, pk: Any) -> HttpResponse:
    """«Jäta kõik vahele»: skip every document still waiting on one Teema (owner, 2026-11-22).

    For when the ones that matter have been added: the rest go in one press. Only
    the reader's own waiting findings on a Teema they may see; each is skipped
    through `watch.dismiss`, exactly as one «Jäta vahele» would.
    """
    waiting = list(watch.pending_for(request.user).filter(matter_id=pk))
    if not waiting:
        raise Http404("Ootel dokumente ei leitud.")
    skipped = 0
    for finding in waiting:
        try:
            watch.dismiss(finding, actor=request.user)
            skipped += 1
        except DomainError:
            continue  # decided by somebody else meanwhile
    messages.success(
        request,
        "1 dokument jäeti vahele." if skipped == 1 else f"{skipped} dokumenti jäeti vahele.",
    )
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
    if request.POST.get("tagasi") == "teema":
        return HttpResponseRedirect(reverse("matters:matter_registers", kwargs={"pk": matter.pk}))
    query = urlencode({"teema": matter.display_reference})
    return HttpResponseRedirect(f"{reverse('register_watch:review')}?{query}")
