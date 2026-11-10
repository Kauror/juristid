"""Haldus → Andmete sünkroonimine → OneNote'i ootel kirjed: held readings, one decision each.

A small review path, not a document-management tool (live-sync round 2). The
importer holds what it cannot decide (``onenote_capture``); this page lists those
items and lets a verified technical administrator decide each one:

* GET (no ``kirje``) — the open items (or, with ``otsustatud=1``, the settled ones),
  filtered by why they are held, a page at a time. Counts per reason. Reads only.
* GET ``?kirje=KEY`` — one item: which page and file, what changed, why it is
  held, the identity Juristid holds and the later capture's, whether the original
  bytes and context are available, the candidate Teemad of a shared page, earlier
  decisions, and one form per decision the item allows.
* POST ``kirje=KEY`` — one decision (``onenote_coverage.decide_hold``): CSRF, the
  actor from the verified administrator session, a mandatory reason, the item's
  token (a changed item is refused), a fresh proof like every high-impact change.

Same gate as the rest of the section: ``may_open_system`` through the shell's
section registry, 404 otherwise. Teemad are named only for a persona that may open
them (``Matter.objects.visible_to``); the others are counted, never named, and an
item on such a Teema is a 404.
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
from app.haldus.data_sync_views import sync_pages
from app.haldus.sections import visible_sections
from app.legacy_import import onenote_coverage
from app.legacy_import.onenote_coverage_models import HoldDecisionKind

SECTION = "andmed"
NOT_HERE = "Lehte ei leitud."
FRESH_PROOF = "Kinnita enne otsustamist uuesti oma isik."
PER_PAGE = 50

#: The reason filter's values in the address: (value, category).
FILTERS = (
    ("omanik", onenote_coverage.HOLD_CATEGORY_OWNERSHIP),
    ("baidid", onenote_coverage.HOLD_CATEGORY_BYTES),
    ("metaandmed", onenote_coverage.HOLD_CATEGORY_METADATA),
    ("muu", onenote_coverage.HOLD_CATEGORY_OTHER),
)

#: What each decision does, said on its button's form before anybody presses.
EFFECT: dict[str, str] = {
    HoldDecisionKind.ASSOCIATE: (
        "Fail lisatakse valitud teema dokumendiks tavalise impordi kaudu (OneNote'i "
        "lisamise päeval). Teised selle lehega seotud teemad faili ei saa."
    ),
    HoldDecisionKind.ACCEPT_REVISION: (
        "Uued baidid lisatakse sama dokumendi järgmiseks versiooniks; varasem versioon "
        "jääb muutmata."
    ),
    HoldDecisionKind.REJECT: (
        "Seos või lugemine lükatakse tagasi; Juristidesse midagi peale otsuse ei kirjutata."
    ),
    HoldDecisionKind.KEEP_HOLDING: (
        "Kirje jääb ootele, kuni tõendeid lisandub; otsus salvestatakse ajalukku."
    ),
}


def _require(request: HttpRequest) -> None:
    if not any(item.key == SECTION for item in visible_sections(request)):
        raise Http404(NOT_HERE)
    admin_session.note_activity(request)


def _context(request: HttpRequest, **extra: Any) -> dict[str, Any]:
    return {
        "haldus_sections": visible_sections(request),
        "haldus_active": SECTION,
        "nav_active": "haldus",
        "sync_pages": sync_pages(""),
        **extra,
    }


def _page_number(request: HttpRequest) -> int:
    try:
        return max(1, int(request.GET.get("leht", "1")))
    except ValueError:
        return 1


def _item_url(key: str) -> str:
    return f"{reverse('haldus:onenote_holds')}?{urlencode({'kirje': key})}"


@never_cache
@require_http_methods(["GET", "HEAD", "POST"])
def holds(request: HttpRequest) -> HttpResponseBase:
    _require(request)
    if request.method == "POST":
        return _decide(request)
    key = request.GET.get("kirje", "")
    if key:
        return _item(request, key)
    return _list(request)


def _list(request: HttpRequest) -> HttpResponse:
    chosen = request.GET.get("liik", "")
    category = dict(FILTERS).get(chosen, "")
    decided = request.GET.get("otsustatud") == "1"
    items, hidden, counts = onenote_coverage.held_items(
        viewer=request.user, category=category, decided=decided
    )
    total = len(items)
    pages = max(1, -(-total // PER_PAGE))
    page = min(_page_number(request), pages)
    filters = [
        {
            "value": value,
            "label": onenote_coverage.HOLD_CATEGORY_LABELS[cat],
            "count": counts.get(cat, 0),
            "active": value == chosen,
        }
        for value, cat in FILTERS
    ]
    return render(
        request,
        "haldus/onenote_holds.html",
        _context(
            request,
            items=items[(page - 1) * PER_PAGE : page * PER_PAGE],
            hidden=hidden,
            total=total,
            page=page,
            pages=pages,
            filters=filters,
            chosen=chosen,
            decided=decided,
            open_total=sum(counts.values()),
        ),
    )


def _item(request: HttpRequest, key: str) -> HttpResponse:
    try:
        item = onenote_coverage.held_item(key, viewer=request.user)
    except onenote_coverage.HoldNotFound:
        raise Http404(NOT_HERE) from None
    forms = [
        {
            "decision": str(option),
            "label": HoldDecisionKind(option).label,
            "effect": EFFECT[option],
            "needs_teema": option == HoldDecisionKind.ASSOCIATE,
            "blocked": _blocked(item, option),
        }
        for option in item.options
    ]
    return render(
        request,
        "haldus/onenote_holds_item.html",
        _context(
            request,
            item=item,
            token=onenote_coverage.item_token(item),
            forms=forms,
        ),
    )


def _blocked(item: onenote_coverage.HeldItem, option: str) -> str:
    """Why a decision would be refused now, said before anybody types a reason."""
    if option in (HoldDecisionKind.ASSOCIATE, HoldDecisionKind.ACCEPT_REVISION):
        if not item.bytes_available:
            return onenote_coverage.HOLD_BYTES_UNAVAILABLE
    if option == HoldDecisionKind.ACCEPT_REVISION:
        if item.newer_revision:
            return onenote_coverage.HOLD_SUPERSEDED
        if item.version_target is None:
            return onenote_coverage.HOLD_NO_DOCUMENT
    return ""


def _decide(request: HttpRequest) -> HttpResponseBase:
    key = request.POST.get("kirje", "")
    actor = admin_session.administrator_actor(request)
    if actor is None or not key:
        raise Http404(NOT_HERE)
    if admin_session.needs_fresh_proof(request):
        from app.accounts.admin_views import ask_for_fresh_proof

        return ask_for_fresh_proof(request, _item_url(key), message=FRESH_PROOF, keep=["reason"])
    try:
        onenote_coverage.decide_hold(
            key,
            actor=actor,
            viewer=request.user,
            decision=request.POST.get("decision", ""),
            reason=request.POST.get("reason", ""),
            token=request.POST.get("token", ""),
            link_id=request.POST.get("teema", ""),
            client={
                "ip_address": client_ip(request),
                "user_agent": request.META.get("HTTP_USER_AGENT", ""),
            },
        )
    except onenote_coverage.HoldNotFound:
        raise Http404(NOT_HERE) from None
    except onenote_coverage.HoldDecisionRefused as refusal:
        messages.error(request, str(refusal))
        return redirect(_item_url(key))
    messages.success(request, "Otsus on salvestatud.")
    return redirect(_item_url(key))
