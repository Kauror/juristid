"""`{{ version|file_link }}` — the open-or-download decision, for a template.

A filter over `app.documents.inline.file_link`, so every template that prints a
stored file's name links it the same way: a PDF, an image or plain text opens
in a new tab, everything else downloads. The decision reads the version's own
filename and MIME type, which the template already holds, and asks the
database nothing.
"""

from __future__ import annotations

from typing import Any

from django import template
from django.utils.safestring import SafeString, mark_safe

from app.documents.inline import FileLink
from app.documents.inline import file_link as build_file_link

register = template.Library()

#: The attributes of a link that opens in a new tab. `noreferrer` as well as
#: `noopener`: the inline response already sends no referrer, and the link
#: should not either.
NEW_TAB_ATTRS = ' target="_blank" rel="noopener noreferrer"'

#: What a screen reader hears at the end of such a link — the same words the
#: chronology's external links have always said (development_row.html).
NEW_TAB_NOTE = '<span class="visually-hidden"> — avaneb uues aknas</span>'


@register.filter
def file_link(version: Any) -> FileLink | None:
    """The link for a `DocumentVersion`, or ``None`` for no version at all."""
    if version is None or not getattr(version, "pk", None):
        return None
    return build_file_link(version)


@register.simple_tag
def new_tab_attrs(opens_inline: Any) -> SafeString:
    """`target`/`rel` for a link that opens inline, and nothing for one that downloads.

    ``{% new_tab_attrs file.opens_inline %}`` inside the `<a …>` tag. One tag
    rather than the attributes typed out per template, so every file link says
    the same thing and the announcement below cannot be forgotten on one.
    """
    return mark_safe(NEW_TAB_ATTRS if opens_inline else "")  # noqa: S308 - constant


@register.simple_tag
def new_tab_note(opens_inline: Any) -> SafeString:
    """The hidden «avaneb uues aknas» at the end of the link's text, when it does."""
    return mark_safe(NEW_TAB_NOTE if opens_inline else "")  # noqa: S308 - constant
