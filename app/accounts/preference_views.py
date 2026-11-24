"""Saving the landing page (app/accounts/landing.py, docs/adr/0221).

One POST route for the person the request is signed in as and nobody else:
there is no parameter naming a person, so a crafted request can only change its
own sender's choice. The value is a key from the allowlist, checked against
what the person may open; anything else is refused and writes nothing.
CSRF-protected like every other write.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods

from app.accounts import landing


@login_required
@require_http_methods(["POST"])
def save_landing_page(request: HttpRequest) -> HttpResponse:
    """«Avaleht»: store one allowed key; back to Isiklikud seaded → Kuvamine."""
    try:
        page = landing.save(request.user, request.POST.get("avaleht", ""))
    except landing.LandingRefused as refusal:
        messages.error(request, str(refusal))
        return redirect("haldus:settings")
    messages.success(request, f"Avaleht on nüüd {page.label}.")
    return redirect("haldus:settings")
