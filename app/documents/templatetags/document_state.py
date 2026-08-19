"""Turning extraction states into Estonian a lawyer can act on.

Filters rather than a context variable, because the state appears in a table row
inside a loop and the alternative is annotating every list query in three views
with the same mapping.

The mapping itself lives in `app.documents.preview` so the detail page and the
list say the same words. Two places deciding what `FAILED` looks like is how a
document reads "Ebaõnnestus" on one screen and "Teksti ei õnnestunud eraldada"
on the next.
"""

from __future__ import annotations

from django import template

from app.documents.preview import STATE_LABELS, STATE_TONES

register = template.Library()


@register.filter
def extraction_label(state: str) -> str:
    return STATE_LABELS.get(state, state)


@register.filter
def extraction_tone(state: str) -> str:
    return STATE_TONES.get(state, "quiet")
