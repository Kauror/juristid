"""Where the top bar's «Haldus» item leads, if anywhere (docs/adr/0186).

One item on the bar, never several: the Haldus shell has its own
sub-navigation. Offered to anybody with at least one Haldus section they may
open — a department head's work management, a verified administrator's account
administration or system status — and, under the shared gate, to a persona
whose account has administrative standing, as a way to the administrator
sign-in. That last one is a hint, not a grant: the sign-in asks the person's
own password and code, and every Haldus route asks its own question again.
"""

from __future__ import annotations

from typing import Any

from django import template
from django.urls import reverse

register = template.Library()


@register.simple_tag(takes_context=True)
def haldus_link(context: Any) -> str:
    """The URL the bar's «Haldus» item points at, or "" for no item."""
    request = context.get("request")
    if request is None:
        return ""
    from app.accounts import admin_session
    from app.haldus.sections import visible_sections

    if visible_sections(request):
        return reverse("haldus:index")
    if admin_session.is_available() and admin_session.holds_administrative_standing(
        getattr(request, "user", None)
    ):
        return reverse("haldus:sign_in")
    return ""
