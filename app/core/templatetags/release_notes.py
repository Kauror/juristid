"""`release_sections` for `templates/core/release_notes.html`.

A filter rather than a second context variable, so every caller that renders
the page with `release_days` alone (the view, the tests) gets the same
month grouping without having to compute it (`app/core/release_notes.py`).
"""

from __future__ import annotations

from django import template

from app.core.release_notes import ReleaseDay, ReleaseSections, release_sections

register = template.Library()


@register.filter(name="release_sections")
def release_sections_filter(days: tuple[ReleaseDay, ...]) -> ReleaseSections:
    return release_sections(tuple(days))
