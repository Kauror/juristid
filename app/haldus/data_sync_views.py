"""Haldus → Andmete sünkroonimine: Lovable and OneNote, for a technical administrator.

Every route asks `may_open_system` (a verified technical administrator) through
the shell's section registry and answers 404 otherwise, like Süsteemi seis and
the Lovable page. Reads (GET, HEAD) write nothing. Each POST is CSRF-protected,
names its actor from the verified administrator session (never the persona),
and is audited by `app.legacy_import.sync_admin`:

* «Kontrolli muudatusi» — the existing report-only CHECK (no source read, no
  business write);
* «Sünkrooni nüüd» — after a confirmation that states the current mode, records
  a LOVABLE_SYNC request; the host runner executes it with a fresh source read;
* a decision on one pending difference (`lovable_sync.resolve_field`), after a
  confirmation;
* «OneNote'i impordi kontroll» and «Impordi OneNote'i materjalid» — requests for
  the host runner; the import is bound to the verified plan's digest.

The high-impact three (sync, decision, import) need a fresh proof of the
administrator, like every account change (`admin_session.needs_fresh_proof`).
The web process never reaches Lovable and never reads or imports a capture.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from app.accounts import admin_session
from app.core.client_ip import client_ip
from app.core.errors import DomainError
from app.haldus.sections import visible_sections
from app.legacy_import import sync_admin
from app.legacy_import.lovable_sync_models import SyncMode
from app.legacy_import.sync_requests_models import SyncRequestLane

SECTION = "andmed"
NOT_HERE = "Lehte ei leitud."
FRESH_PROOF = "Kinnita enne käivitamist uuesti oma isik."

#: The sub-pages, in the strip's order: (key, label, url name).
PAGES = (
    ("ulevaade", "Ülevaade", "haldus:data_sync"),
    ("erinevused", "Erinevused", "haldus:data_sync_differences"),
    ("ajalugu", "Ajalugu", "haldus:data_sync_history"),
    ("lovable", "Lovable'i üksikasjad", "haldus:lovable"),
)

#: What a run does in each mode — the confirmation says it before anybody presses.
MODE_EFFECT: dict[str, str] = {
    SyncMode.COMPARE: (
        "Ainult võrdlus: Lovable loetakse uuesti ja tulemused salvestatakse ajalukku. "
        "Juristide teemasid ei muudeta."
    ),
    SyncMode.APPLY: (
        "Võrdlus ja ohutud uuendused: Lovable loetakse uuesti ning ainult lubatud väljade "
        "ohutud muudatused kirjutatakse Juristidesse (kui värske varukoopia on olemas). "
        "Kõik muu jääb ülevaatusele."
    ),
    SyncMode.OFF: "Sünkroonimine on peatatud; käivitust ei saa taotleda.",
}


def recorded_message(lane: str, done: str) -> str:
    """What the page says after recording a request: never that work was done.

    ``done`` says what the runner will do; the rest says honestly whether a
    runner is there to do it (`sync_admin.runner_status`)."""
    runner = sync_admin.runner_status(lane)
    if runner.active:
        soon = "Taotlus on salvestatud; serveri käivitaja võtab selle üles mõne minuti jooksul."
        return f"{soon} {done}"
    return f"Taotlus on salvestatud, kuid seda pole veel käivitatud. {runner.note}"


def _require(request: HttpRequest) -> None:
    if not any(item.key == SECTION for item in visible_sections(request)):
        raise Http404(NOT_HERE)
    admin_session.note_activity(request)


def _client(request: HttpRequest) -> dict[str, Any]:
    return {
        "ip_address": client_ip(request),
        "user_agent": request.META.get("HTTP_USER_AGENT", ""),
    }


def _actor(request: HttpRequest) -> Any:
    actor = admin_session.administrator_actor(request)
    if actor is None:
        raise Http404(NOT_HERE)
    return actor


def _context(request: HttpRequest, page: str, **extra: Any) -> dict[str, Any]:
    return {
        "haldus_sections": visible_sections(request),
        "haldus_active": SECTION,
        "nav_active": "haldus",
        "sync_pages": sync_pages(page),
        **extra,
    }


def sync_pages(active: str) -> list[dict[str, Any]]:
    """The section's own strip of sub-pages; ``active`` names the current one."""
    return [
        {"key": key, "label": label, "url": reverse(name), "active": key == active}
        for key, label, name in PAGES
    ]


def _page_number(request: HttpRequest, name: str = "leht") -> int:
    try:
        return max(1, int(request.GET.get(name, "1")))
    except ValueError:
        return 1


def _fresh_proof(request: HttpRequest, back: str) -> HttpResponseBase | None:
    if admin_session.needs_fresh_proof(request):
        from app.accounts.admin_views import ask_for_fresh_proof

        return ask_for_fresh_proof(request, back, message=FRESH_PROOF, keep=["reason", "note"])
    return None


@never_cache
@require_http_methods(["GET", "HEAD"])
def overview(request: HttpRequest) -> HttpResponse:
    """Both panels, the open requests and the actions. Reads only."""
    _require(request)
    return render(
        request,
        "haldus/data_sync.html",
        _context(
            request,
            "ulevaade",
            lovable=sync_admin.lovable_panel(request.user),
            onenote=sync_admin.onenote_panel(),
            mode_effect=MODE_EFFECT.get(sync_admin.current_mode(), ""),
        ),
    )


@never_cache
@require_http_methods(["POST"])
def check(request: HttpRequest) -> HttpResponse:
    """«Kontrolli muudatusi»: the existing report-only CHECK, from this page."""
    from app.legacy_import.lovable_sync import SyncRefused, run_sync
    from app.legacy_import.lovable_sync_models import SyncTrigger

    _require(request)
    actor = _actor(request)
    try:
        run = run_sync(trigger=SyncTrigger.CHECK)
    except SyncRefused as error:
        sync_admin.record_check(actor, run=None, code=error.code, client=_client(request))
        messages.info(request, sync_admin.failure_reason(error.code))
    else:
        sync_admin.record_check(actor, run=run, client=_client(request))
        messages.success(
            request,
            "Kontroll tehtud: viimati loetud Lovable'i seisu võrreldi Juristidega"
            f" ({run.counts.get('checked', 0)} teemat). Ühtegi teemat ei muudetud.",
        )
    return redirect("haldus:data_sync")


@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
def sync_now(request: HttpRequest) -> HttpResponseBase:
    """«Sünkrooni nüüd»: GET shows the confirmation, POST records the request."""
    _require(request)
    back = reverse("haldus:data_sync_now")
    mode = sync_admin.current_mode()
    if request.method == "POST":
        proof = _fresh_proof(request, back)
        if proof is not None:
            return proof
        actor = _actor(request)
        try:
            sync_admin.request_lovable_sync(
                actor,
                request.POST.get("reason", ""),
                mode_seen=request.POST.get("mode_seen", ""),
                client=_client(request),
            )
        except sync_admin.SyncRequestRefused as error:
            messages.error(request, str(error))
            return render(
                request,
                "haldus/data_sync_confirm.html",
                _context(
                    request,
                    "ulevaade",
                    mode=mode,
                    mode_label=SyncMode(mode).label,
                    mode_effect=MODE_EFFECT.get(mode, ""),
                    busy=sync_admin.open_request(SyncRequestLane.LOVABLE),
                    runner=sync_admin.runner_status(SyncRequestLane.LOVABLE),
                    reason=request.POST.get("reason", ""),
                ),
                status=409 if error.code in ("LANE_BUSY", "MODE_CHANGED") else 400,
            )
        messages.success(
            request,
            recorded_message(
                SyncRequestLane.LOVABLE,
                "Server loeb Lovable'i uuesti; tulemus ilmub siia.",
            ),
        )
        return redirect("haldus:data_sync")
    return render(
        request,
        "haldus/data_sync_confirm.html",
        _context(
            request,
            "ulevaade",
            mode=mode,
            mode_label=SyncMode(mode).label,
            mode_effect=MODE_EFFECT.get(mode, ""),
            busy=sync_admin.open_request(SyncRequestLane.LOVABLE),
            runner=sync_admin.runner_status(SyncRequestLane.LOVABLE),
            reason="",
        ),
    )


@never_cache
@require_http_methods(["GET", "HEAD"])
def differences(request: HttpRequest) -> HttpResponse:
    """«Vaata erinevusi»: pending differences, named only where the reader may open them."""
    _require(request)
    return render(
        request,
        "haldus/data_sync_differences.html",
        _context(
            request,
            "erinevused",
            diff=sync_admin.differences(
                request.user,
                page=_page_number(request),
                history_page=_page_number(request, "ajalugu") if "ajalugu" in request.GET else None,
            ),
        ),
    )


@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
def resolve(request: HttpRequest) -> HttpResponseBase:
    """One field's decision: GET confirms, POST decides through `resolve_field`."""
    _require(request)
    source = request.POST if request.method == "POST" else request.GET
    reference = str(source.get("viide", "")).strip()
    field_name = str(source.get("vali", "")).strip()
    state = sync_admin.resolvable(request.user, reference, field_name)
    if state is None:
        raise Http404(NOT_HERE)
    query = urlencode({"viide": reference, "vali": field_name})
    back = f"{reverse('haldus:data_sync_resolve')}?{query}"
    row = sync_admin.difference_for(request.user, reference, field_name)
    context = _context(request, "erinevused", state=state, row=row, field_name=field_name)
    if request.method == "POST":
        proof = _fresh_proof(request, back)
        if proof is not None:
            return proof
        actor = _actor(request)
        try:
            sync_admin.resolve_difference(
                actor,
                request.user,
                reference=reference,
                field_name=field_name,
                note=request.POST.get("note", ""),
                client=_client(request),
            )
        except sync_admin.SyncRequestRefused as error:
            messages.error(request, str(error))
            return render(request, "haldus/data_sync_resolve.html", context, status=400)
        messages.success(
            request,
            "Otsus salvestatud: Juristide väärtus jääb, "
            "Lovable'i muudatus on märgitud läbivaadatuks.",
        )
        return redirect("haldus:data_sync_differences")
    return render(request, "haldus/data_sync_resolve.html", context)


@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
def release_hold(request: HttpRequest) -> HttpResponseBase:
    """A closure from Lovable held for Juristid-only work (TL-02), released by a
    technical administrator: GET states what would be closed and cancelled,
    POST closes it in their name (`lovable_closure_hold.release_closure_hold`).

    Counts only: an administrator reads no business content (docs/adr/0181), so
    the page names the Teema by its reference and the work by its kinds."""
    from app.legacy_import.lovable_closure_hold import (
        RELEASE_NOT_WRITING,
        release_closure_hold,
        release_preview,
        sync_writes,
    )
    from app.matters.models import Matter

    _require(request)
    if not sync_writes():
        # Only while the synchronisation writes (lead decision 12.11.2026).
        messages.info(request, RELEASE_NOT_WRITING)
        return redirect("haldus:lovable")
    source = request.POST if request.method == "POST" else request.GET
    reference = str(source.get("viide", "")).strip()
    year, _sep, number = reference.partition("_")
    matter = (
        Matter.objects.filter(reference_year=int(year), reference_number=int(number)).first()
        if year.isdigit() and number.isdigit()
        else None
    )
    preview = release_preview(matter) if matter is not None else None
    if matter is None or preview is None:
        messages.info(request, "Sellel teemal pole ootel sulgemist.")
        return redirect("haldus:lovable")
    back = f"{reverse('haldus:data_sync_release_hold')}?{urlencode({'viide': reference})}"
    context = _context(request, "lovable", reference=reference, preview=preview)
    if request.method == "POST":
        proof = _fresh_proof(request, back)
        if proof is not None:
            return proof
        actor = _actor(request)
        try:
            result = release_closure_hold(
                matter=matter, actor=actor, confirmed=request.POST.get("kinnitus", "")
            )
        except DomainError as error:
            messages.error(request, str(error))
            return render(request, "haldus/data_sync_release_hold.html", context, status=400)
        (messages.success if result.closed else messages.info)(request, result.message)
        return redirect("haldus:lovable")
    return render(request, "haldus/data_sync_release_hold.html", context)


@never_cache
@require_http_methods(["GET", "HEAD"])
def history(request: HttpRequest) -> HttpResponse:
    """«Vaata ajalugu»: every run and every request, newest first, a page at a time."""
    _require(request)
    page = _page_number(request)
    return render(
        request,
        "haldus/data_sync_history.html",
        _context(
            request,
            "ajalugu",
            runs=sync_admin.history(page=page),
            requests=sync_admin.request_history(page=page),
        ),
    )


@never_cache
@require_http_methods(["POST"])
def onenote_verify(request: HttpRequest) -> HttpResponse:
    """«OneNote'i impordi kontroll»: a request to check one named capture on the host."""
    _require(request)
    actor = _actor(request)
    try:
        sync_admin.request_onenote_verify(
            actor,
            request.POST.get("capture", ""),
            request.POST.get("reason", ""),
            client=_client(request),
        )
    except sync_admin.SyncRequestRefused as error:
        messages.error(request, str(error))
    else:
        messages.success(
            request,
            recorded_message(
                SyncRequestLane.ONENOTE, "Server kontrollib kogumit; tulemus ilmub siia."
            ),
        )
    return redirect(f"{reverse('haldus:data_sync')}#onenote")


@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
def onenote_import(request: HttpRequest) -> HttpResponseBase:
    """«Impordi OneNote'i materjalid»: GET confirms a verified capture, POST records the import."""
    _require(request)
    source = request.POST if request.method == "POST" else request.GET
    capture = sync_admin.valid_capture_name(str(source.get("capture", "")))
    if capture is None:
        raise Http404(NOT_HERE)
    plan = sync_admin.verified_plan(capture)
    check = next(
        (v for v in sync_admin.onenote_panel().verified if v.request.capture_name == capture),
        None,
    )
    back = f"{reverse('haldus:data_sync_onenote_import')}?{urlencode({'capture': capture})}"
    context = _context(
        request,
        "ulevaade",
        capture=capture,
        plan=plan,
        check=check,
        busy=sync_admin.open_request(SyncRequestLane.ONENOTE),
        reason=str(source.get("reason", "")),
    )
    if request.method == "POST":
        proof = _fresh_proof(request, back)
        if proof is not None:
            return proof
        actor = _actor(request)
        try:
            sync_admin.request_onenote_import(
                actor,
                capture,
                request.POST.get("plan_sha256", ""),
                request.POST.get("reason", ""),
                client=_client(request),
            )
        except sync_admin.SyncRequestRefused as error:
            messages.error(request, str(error))
            return render(request, "haldus/data_sync_import.html", context, status=409)
        messages.success(
            request,
            recorded_message(
                SyncRequestLane.ONENOTE,
                "Server kontrollib kogumi uuesti ja impordib ainult siis, kui plaan on sama.",
            ),
        )
        return redirect(f"{reverse('haldus:data_sync')}#onenote")
    return render(request, "haldus/data_sync_import.html", context)
