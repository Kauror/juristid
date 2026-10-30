"""The includable pieces other pages carry (docs/adr/0188 §6, docs/adr/0198 §6).

Template tags rather than context-processor keys or view changes, so a page
that wants the bell or the Minu asjad block says so in its template and no
other page pays for it:

* ``{% notifications_bell %}`` — the top-bar «Teavitused» link. It renders **no
  query**: the number arrives by its own request once the bell is visible
  (`notifications:bell`).
* ``{% notifications_block tagasi="minu_asjad" %}`` — the reader's own inbox as
  one rail block, the first few items and a link to the rest; nothing at all
  when it is empty. Personal: it reads the signed-in reader, so it belongs
  inside Minu asjad's ``is_self`` branch and nowhere else.
* ``{% department_commencement_reminders %}`` — commencement prompts on Matters
  with nobody responsible, for `department.view_management` holders; nothing
  otherwise.
"""

from __future__ import annotations

from typing import Any

from django import template

from app.core.authorization import may_read_business_content, may_write_business_content
from app.notifications import inbox, selectors

register = template.Library()

#: How many items the Minu asjad block shows before «Kõik teavitused».
BLOCK_LIMIT = 5


def _user(context: Any) -> Any:
    request = context.get("request")
    return getattr(request, "user", None) if request is not None else context.get("user")


@register.inclusion_tag("notifications/_topbar_bell.html", takes_context=True)
def notifications_bell(context: Any) -> dict[str, Any]:
    user = _user(context)
    return {
        "show": bool(getattr(user, "is_authenticated", False)) and may_read_business_content(user)
    }


@register.inclusion_tag("notifications/_minu_asjad_block.html", takes_context=True)
def notifications_block(context: Any, tagasi: str = "minu_asjad") -> dict[str, Any]:
    user = _user(context)
    items = inbox.inbox_for(user)
    return {
        "items": items[:BLOCK_LIMIT],
        "total": len(items),
        "more": len(items) > BLOCK_LIMIT,
        "can_answer": may_write_business_content(user),
        "tagasi": tagasi,
        "csrf_token": context.get("csrf_token"),
    }


@register.inclusion_tag("notifications/_department_block.html", takes_context=True)
def department_commencement_reminders(context: Any) -> dict[str, Any]:
    user = _user(context)
    return {"rows": selectors.department_unowned_reminders(user)}
