"""Haldus → Ajaloolised kirjad: pairing Postipoiss letters with Teemad (docs/adr/0216).

One list with filters, one POST route for a decision. Reading follows the
section predicate (business read); deciding is a business write and refuses on
its own with the 404 every write surface uses. A suggested or chosen Teema is
shown only through `postipoiss.visible_matter`, so a restricted Teema's title
never reaches a reader who may not see it.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from app.core.decorators import business_write_required
from app.core.errors import DomainError
from app.core.text import folded_contains
from app.haldus.views import _context, _require
from app.legacy_import import postipoiss
from app.legacy_import.postipoiss_models import PairingState, PairingTier, PostipoissPairing

PAGE_SIZE = 50
STATES = {
    "ootel": PairingState.PENDING,
    "seotud": PairingState.APPLIED,
    "taust": PairingState.BACKGROUND,
}
STATE_LABELS = (
    ("ootel", "Ootab otsust"),
    ("seotud", "Seotud"),
    ("taust", "Taustaks"),
    ("koik", "Kõik"),
)


def _filters(source: Any) -> dict[str, str]:
    state = str(source.get("olek", "") or "ootel")
    tier = str(source.get("kindlus", "") or "")
    year = str(source.get("aasta", "") or "")
    page = str(source.get("lk", "") or "1")
    return {
        "olek": state if state in STATES or state == "koik" else "ootel",
        "kindlus": tier if tier in PairingTier.values else "",
        "aasta": year if year.isdigit() else "",
        "q": str(source.get("q", "") or "").strip()[:100],
        "lk": page if page.isdigit() and int(page) > 0 else "1",
    }


def _filtered(request: HttpRequest, chosen: dict[str, str]) -> Any:
    rows = postipoiss.pairings_for(request.user)
    if chosen["olek"] in STATES:
        rows = rows.filter(state=STATES[chosen["olek"]])
    if chosen["kindlus"]:
        rows = rows.filter(tier=chosen["kindlus"])
    if chosen["aasta"]:
        rows = rows.filter(letter__year=int(chosen["aasta"]))
    if chosen["q"]:
        q = chosen["q"]
        rows = rows.filter(
            folded_contains(
                q, "letter__kirja_id", "letter__title", "letter__recipient", "letter__body"
            )
        )
    return rows.order_by("-letter__year", "letter__register_row", "letter__kirja_id")


def _query(chosen: dict[str, str], **override: str) -> str:
    merged = {**chosen, **override}
    return urlencode({key: value for key, value in merged.items() if value and value != "1"})


@never_cache
@login_required
@require_http_methods(["GET"])
def letters(request: HttpRequest) -> HttpResponse:
    _require(request, "kirjad")
    chosen = _filters(request.GET)
    rows = _filtered(request, chosen)
    total = rows.count()
    page = int(chosen["lk"])
    shown = list(rows[(page - 1) * PAGE_SIZE : page * PAGE_SIZE])
    every = PostipoissPairing.objects
    counts = {key: every.filter(state=state).count() for key, state in STATES.items()}
    years = sorted(
        {y for y in every.values_list("letter__year", flat=True).distinct() if y}, reverse=True
    )
    user = request.user
    return render(
        request,
        "haldus/letters.html",
        _context(
            request,
            "kirjad",
            filters=chosen,
            states=[(key, label, counts.get(key)) for key, label in STATE_LABELS],
            tiers=list(PairingTier.choices),
            years=years,
            total=total,
            rows=[
                {
                    "pairing": pairing,
                    "letter": pairing.letter,
                    "files": list(pairing.letter.files.all()),
                    "suggested": postipoiss.visible_matter(user, pairing.suggested_matter),
                    "chosen": postipoiss.visible_matter(user, pairing.matter),
                }
                for pairing in shown
            ],
            previous_url=f"?{_query(chosen, lk=str(page - 1))}" if page > 1 else "",
            next_url=f"?{_query(chosen, lk=str(page + 1))}" if page * PAGE_SIZE < total else "",
        ),
    )


def _back(request: HttpRequest) -> HttpResponseRedirect:
    chosen = _filters(request.POST)
    url = reverse("haldus:letters")
    query = _query(chosen)
    return HttpResponseRedirect(f"{url}?{query}" if query else url)


@never_cache
@login_required
@require_http_methods(["POST"])
@business_write_required
def decide(request: HttpRequest, pk: Any) -> HttpResponse:
    """«Kinnita», «Seo teemaga» (viide), «Jäta taustaks» or «Võta tagasi»."""
    _require(request, "kirjad")
    pairing = PostipoissPairing.objects.filter(pk=pk).first()
    if pairing is None:
        raise Http404("Kirja ei leitud.")
    decision = str(request.POST.get("otsus", "") or "")
    try:
        if decision == "kinnita":
            outcome = postipoiss.accept(pairing, actor=request.user)
            messages.success(request, _said(pairing, outcome))
        elif decision == "vali":
            outcome = postipoiss.choose(
                pairing, actor=request.user, reference=str(request.POST.get("viide", ""))
            )
            messages.success(request, _said(pairing, outcome))
        elif decision == "taust":
            postipoiss.leave_as_background(
                pairing, actor=request.user, note=str(request.POST.get("markus", ""))
            )
            messages.success(request, f"{pairing.letter.kirja_id} jäeti taustaks.")
        elif decision == "taasta":
            postipoiss.reopen(pairing, actor=request.user)
            messages.success(request, f"{pairing.letter.kirja_id} ootab jälle otsust.")
        else:
            raise DomainError("Tundmatu otsus.")
    except DomainError as refusal:
        messages.error(request, str(refusal))
    return _back(request)


def _said(pairing: PostipoissPairing, outcome: postipoiss.ApplyOutcome) -> str:
    kirja_id = pairing.letter.kirja_id
    if outcome.joined_existing:
        return f"{kirja_id} seoti teemaga; teemal oli selle kirja arvamus juba olemas."
    if outcome.submission_created:
        return f"{kirja_id} seoti teemaga ja lisati väljasaadetud arvamuseks."
    return f"{kirja_id} seoti teemaga (failita kiri, arvamust ei lisatud)."
