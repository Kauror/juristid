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

from app.accounts import disclosures

register = template.Library()


@register.simple_tag(takes_context=True)
def disclosure(context: Any, key: str) -> str:
    opened = disclosures.is_open(context.get("request"), key)
    return format_html(
        'data-disclosure="{}" data-disclosure-saved="{}"{}',
        key,
        "1" if opened else "0",
        format_html(" open") if opened else "",
    )
