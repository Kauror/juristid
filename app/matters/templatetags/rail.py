"""``{% rail_tip step lovable_commencement_ids as tip %}`` — one rail item's line and popover.

`app.matters.rail_tips.tip_for` over what the page already holds; no query.
"""

from __future__ import annotations

from typing import Any

from django import template

from app.matters.rail_tips import LOVABLE_OWNED_NOTE, RailTip, tip_for

register = template.Library()


@register.simple_tag
def rail_tip(step: Any, lovable_ids: Any = None) -> RailTip:
    return tip_for(step, frozenset(lovable_ids or ()))


@register.simple_tag
def rail_lovable_note() -> str:
    return LOVABLE_OWNED_NOTE
