"""The top bar's way into Haldus, and the administrator session's line in the persona menu.

**The gear** (`haldus_link`). An entry in the settings menu behind the gear
beside «Välju» (UI-03, templates/components/settings_menu.html), never a text
item in the everyday navigation: Haldus is where somebody goes now and then, not a
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
def notification_settings_link(context: Any) -> str:
    """The URL of the settings menu's «Teavituste seaded», or "" for none (UI-03).

    Offered to exactly the readers the bell is drawn for — a signed-in
    internal reader (`notifications_bell`) — because the page is about the
    notices that bell counts. Showing it is not what protects it: the view is
    `login_required` and reads only the signed-in person's own choices.
    """
    request = context.get("request")
    user = getattr(request, "user", None) if request is not None else None
    if not getattr(user, "is_authenticated", False):
        return ""
    from app.core.authorization import may_read_business_content

    return reverse("notifications:settings") if may_read_business_content(user) else ""


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
