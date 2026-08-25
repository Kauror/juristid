"""Reading one stage's explanation out of the mapping a view already built.

A filter rather than a tag that queries, because the alternative is eleven
lookups on a page rendering eleven chips. ``app.workflow.selectors`` reads the
explanations once; this only picks one out.
"""

from __future__ import annotations

from typing import Any

from django import template

register = template.Library()


@register.filter
def stage_help(mapping: Any, value: Any) -> str:
    """The explanation for ``value``, or "" when the stage has none.

    ``value`` arrives as whatever a rendered choice carries — a
    ``ModelChoiceIteratorValue`` for a model-backed radio, ``""`` for the named
    blank option — so it is stringified here rather than at every call site.
    """
    if not mapping:
        return ""
    return mapping.get(str(value), "")
