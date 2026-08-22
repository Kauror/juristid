"""Presentation helper for the numbered pager.

`Paginator.get_elided_page_range` needs the current page number, and a Django
template cannot call a method with an argument. Rather than add the same line to
every view that paginates, the pager asks for it here — this is layout, not a
decision about what to show.
"""

from __future__ import annotations

from collections.abc import Iterator

from django import template
from django.core.paginator import Page

register = template.Library()


@register.simple_tag
def elided_page_range(page: Page, on_each_side: int = 2, on_ends: int = 1) -> Iterator[str | int]:
    """The page numbers to offer, with Paginator.ELLIPSIS where a run is cut.

    A fixed-width widget whether the register holds three pages or four hundred.
    """
    return page.paginator.get_elided_page_range(
        page.number, on_each_side=on_each_side, on_ends=on_ends
    )
