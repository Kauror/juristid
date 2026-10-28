"""The top bar's way into Haldus, and the administrator session's line in the persona menu.

**The gear** (`haldus_link`). One icon beside «Välju», never a text item in the
everyday navigation: Haldus is where somebody goes now and then, not a
destination in the daily rotation (the owner's brief, 2026-10-28). It leads to
Haldus → Ülevaade, which since then opens for **every** signed-in persona or
account — it carries *Rakendus* (Uuendused, Kiirklahvid, the running build,
«Taasta plokkide vaikeseaded») — and for a persona-less gate session holding a
verified administrator session (`app.haldus.sections.may_open_overview`).

**It still says nothing about who the administrators are.** It used to be shown
only where a section opened, so that a persona's administrative standing could
not be read off the bar by switching personas (docs/adr/0186, security review
P3b). Now it is shown to every persona alike, which keeps that property more
simply: the landing page's privileged sections follow the verified
administrator session, never the persona, and every route behind them refuses
on its own.

**The administrator session** (`verified_administrator_session`). The persona
menu's bottom section offers «Halduri sisselogimine» — the real step-up, the
person's own password and code — to every reader alike, or, while this browser
holds a verified session, names whose it is and how to end it. Read from
`admin_session.verified_administrator`, which never derives anything from the
persona.
"""

from __future__ import annotations

from typing import Any

from django import template
from django.urls import reverse

register = template.Library()


@register.simple_tag(takes_context=True)
def haldus_link(context: Any) -> str:
    """The URL the bar's Haldus gear points at, or "" for no gear."""
    request = context.get("request")
    if request is None:
        return ""
    from app.haldus.sections import visible_sections

    return reverse("haldus:index") if visible_sections(request) else ""


@register.simple_tag(takes_context=True)
def verified_administrator_session(context: Any) -> Any:
    """The verified administrator principal of this request, or None.

    Only under the shared gate, where the step-up is a separate session beside
    the persona; under personal sign-in the account *is* the administrator and
    the bar has no persona menu (docs/adr/0145, 0186).
    """
    request = context.get("request")
    if request is None:
        return None
    from app.accounts import admin_session

    if not admin_session.is_available():
        return None
    return admin_session.verified_administrator(request)
