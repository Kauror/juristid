"""Naming a colleague inside a list the template already has.

`app.accounts.naming` answers "what do I call this person so the reader can tell
them from the others", which needs the *others*. Django's template language
cannot call a function with an argument, so the filter does that and nothing
else — the same shape `app/matters/templatetags/matter_activity.py` uses, and
for the same reason.

Deliberately not a property on `User`. A person's label depends on who they are
being listed beside; a property would have to answer without knowing, which is
exactly the global rename this is here to avoid (pilot QA F-03).
"""

from __future__ import annotations

from typing import Any

from django import template

from app.accounts import naming
from app.core.visibility_help import RESTRICTED_VISIBILITY_HELP

register = template.Library()


@register.filter(name="name_among")
def name_among(person: Any, people: Any) -> str:
    """What to call ``person`` so ``people`` can be told apart."""
    return naming.name_among(person, people or [])


@register.filter(name="full_name_among")
def full_name_among(person: Any, people: Any) -> str:
    """The same, for a surface that already shows whole names.

    `Vali kasutaja` asks who you *are*; abbreviating it to first names to save
    space would answer a smaller question. It still needs the last rung when two
    accounts carry the same display name.
    """
    return naming.name_among(person, people or [], start_full=True)


@register.simple_tag(name="restricted_visibility_help")
def restricted_visibility_help() -> str:
    """The one explanation of `Piiratud` (app/core/visibility_help.py)."""
    return RESTRICTED_VISIBILITY_HELP
