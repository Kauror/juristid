"""Saving and resetting remembered blocks (app/accounts/disclosures.py, docs/adr/0194).

Two POST routes, both for the person the request is signed in as and nobody
else: there is no parameter naming a person, so a crafted request can only ever
change its own sender's presentation state. Both are CSRF-protected like every
other write.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from app.accounts import disclosures
from app.core.request_params import safe_local_path


@login_required
@require_http_methods(["POST"])
def save_disclosure(request: HttpRequest) -> HttpResponse:
    """Remember one block as open (``open=1``) or closed (``open=0``).

    204 on success — the script treats nothing else as saved. An unregistered
    key or any other value of ``open`` is a 400 and writes nothing, so the table
    stays bounded by the registry.
    """
    key = request.POST.get("key", "")
    state = request.POST.get("open", "")
    if not disclosures.is_registered(key) or state not in ("0", "1"):
        return HttpResponseBadRequest("Tundmatu plokk.")
    disclosures.remember(request.user, key, opened=state == "1")
    return HttpResponse(status=204)


@login_required
@require_http_methods(["POST"])
def reset_disclosures(request: HttpRequest) -> HttpResponse:
    """«Taasta plokkide vaikeseaded»: forget every choice; back where it was asked."""
    disclosures.reset(request.user)
    messages.success(request, "Plokid avanevad jälle vaikimisi viisil.")
    target = safe_local_path(request.POST.get("next"), host=request.get_host())
    return HttpResponseRedirect(target or reverse("haldus:index"))
