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

from app.documents.inline import FileLink
from app.documents.inline import file_link as build_file_link

register = template.Library()


@register.filter
def file_link(version: Any) -> FileLink | None:
    """The link for a `DocumentVersion`, or ``None`` for no version at all."""
    if version is None or not getattr(version, "pk", None):
        return None
    return build_file_link(version)
