"""Where the top bar's «Haldus» item leads, if anywhere (docs/adr/0186).

One item on the bar, never several: the Haldus shell has its own
sub-navigation. Offered to anybody with at least one Haldus section they may
open — a department head's work management, a verified administrator's account
administration or system status — and to nobody else.

**Never on administrative standing alone.** Showing the item to a persona
whose account *holds* `accounts.manage` would let anybody with the shared
password find out who the administrators are by switching personas. So a
persona's administrative capabilities change nothing on the bar; the way to the
administrator sign-in is the same quiet link for every reader, on the persona
page and on Haldus → Ülevaade, and it asks the person's own password and code.
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
    from app.haldus.sections import visible_sections

    return reverse("haldus:index") if visible_sections(request) else ""
