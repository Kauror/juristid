"""The two includable pieces other pages carry (docs/adr/0188 §6).

Template tags rather than context-processor keys or view changes, so a page
that wants the bell or the Minu asjad block says so in its template and no
other page pays for it:

* ``{% commencement_bell %}`` — the top-bar link. It renders **no query**: the
  number arrives by its own request once the bell is visible
  (`notifications:bell`).
* ``{% commencement_reminders_block tagasi="minu_asjad" %}`` — the reader's own
  open prompts as a rail block; nothing at all when there are none.
* ``{% department_commencement_reminders %}`` — prompts on Matters with nobody
  responsible, for `department.view_management` holders; nothing otherwise.
"""

from __future__ import annotations

from typing import Any

from django import template

from app.core.authorization import may_read_business_content, may_write_business_content
from app.notifications import selectors

register = template.Library()


def _user(context: Any) -> Any:
    request = context.get("request")
    return getattr(request, "user", None) if request is not None else context.get("user")


@register.inclusion_tag("notifications/_topbar_bell.html", takes_context=True)
def commencement_bell(context: Any) -> dict[str, Any]:
    user = _user(context)
    return {
        "show": bool(getattr(user, "is_authenticated", False)) and may_read_business_content(user)
    }


@register.inclusion_tag("notifications/_minu_asjad_block.html", takes_context=True)
def commencement_reminders_block(context: Any, tagasi: str = "minu_asjad") -> dict[str, Any]:
    user = _user(context)
    return {
        "rows": selectors.open_reminders_for(user),
        "can_answer": may_write_business_content(user),
        "tagasi": tagasi,
        "csrf_token": context.get("csrf_token"),
    }


@register.inclusion_tag("notifications/_department_block.html", takes_context=True)
def department_commencement_reminders(context: Any) -> dict[str, Any]:
    user = _user(context)
    return {"rows": selectors.department_unowned_reminders(user)}
