"""Reading the per-row source instruction map inside a shared partial.

``matters/partials/matter_table.html`` is rendered by four different surfaces,
so the instruction text is gathered once by the view — one query for the whole
page — and handed down as a mapping. Django's template language cannot subscript
a dict by a variable key, hence this filter, which does that and nothing else.

Absent is the normal case and returns "", so a page whose view does not supply
the mapping simply shows the ordinary empty state rather than erroring.
"""

from __future__ import annotations

from typing import Any

from django import template

register = template.Library()


@register.filter
def get_item(mapping: Any, key: Any) -> str:
    if not isinstance(mapping, dict):
        return ""
    return mapping.get(key, "")
