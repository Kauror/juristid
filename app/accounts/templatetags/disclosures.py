"""``{% disclosure "key" %}`` — the attributes of a remembered block's ``<details>``.

    <details class="foldout" {% disclosure "my_work.statistics" %}>

renders ``data-disclosure="my_work.statistics" data-disclosure-saved="1" open``
(``open`` only when the block is open for this person). The state is decided on
the server, so the block is drawn the right way round before any script runs,
and with no script at all it is an ordinary ``<details>`` that still opens and
closes (static/js/disclosures.js, docs/adr/0194).
"""

from __future__ import annotations

from typing import Any

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from app.accounts import disclosures

register = template.Library()


@register.simple_tag(takes_context=True)
def disclosure(context: Any, key: str, force_open: bool = False) -> str:
    """The block's attributes; ``force_open`` draws it open for this view only.

    For a block the reader has explicitly asked to see — the next page of a list
    inside it, say — whatever they chose to keep it as. The stored choice is not
    touched: ``data-disclosure-shown`` tells the script that this ``open`` is the
    page's doing, so the browser's own ``toggle`` for it is not saved as a choice
    (static/js/disclosures.js).
    """
    opened = disclosures.is_open(context.get("request"), key)
    shown = ' data-disclosure-shown="1"' if force_open and not opened else ""
    return format_html(
        'data-disclosure="{}" data-disclosure-saved="{}"{}{}',
        key,
        "1" if opened else "0",
        mark_safe(shown),  # noqa: S308 — a constant attribute, nothing interpolated
        mark_safe(" open" if opened or force_open else ""),  # noqa: S308 — a constant
    )
