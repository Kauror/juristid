"""Turning a value in a register row into the filter that selects it.

A reader who sees *Kooskõlastusringil* in the Hetkeseis column and wants the
rest of them has always had to open Täpsem otsing, find the control, choose the
same words and submit. The value is already on screen and the register already
understands the parameter, so the only thing missing was the link.

That link is one address with one parameter replaced, which Django's template
language cannot build on its own: `?{{ query_string }}&hetkeseis={{ key }}`
would append a second `hetkeseis` beside the one already there and leave the
register reading the first of two contradictory values.

So the tag calls :func:`app.matters.register_filters.register_query` — the same
function the column headings' own links are built from — rather than owning a
second idea of what a register address is. Everything not named survives (`q`,
the sort, every other filter) and `leht` is dropped, because a narrower list
starts at its first page.

The tag renders a **query string**, never a whole URL. The register answers on
one address and every link on the page is written `?…#tulemused`; a tag that
produced an absolute path would be the one link that stopped working the day
the route moved (app/matters/views.py, `RESULTS_ANCHOR`).
"""

from __future__ import annotations

from typing import Any

from django import template
from django.http import QueryDict

from app.matters.register_filters import register_query

register = template.Library()


@register.simple_tag(takes_context=True, name="register_filter_query")
def register_filter_query(context: Any, name: str, value: Any) -> str:
    """The current address with ``name`` set to ``value``, as a query string.

    Reads the live ``request.GET`` rather than a copy passed down through the
    template, so a row rendered inside the live search's fragment carries the
    same parameters the whole page does.
    """
    request = context.get("request")
    params = request.GET if request is not None else QueryDict()
    return register_query(params, **{name: str(value)})
