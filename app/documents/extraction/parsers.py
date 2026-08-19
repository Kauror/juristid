"""Importing this module is what puts parsers in the registry.

Registration happens as an import side effect, which is worth being explicit
about because side effects at import are usually a smell. Here it buys one
thing: a new format is added by writing one module and adding one line below,
with no second list to keep in step. A parser that exists but was never imported
would silently mean "this format is unsupported", so the import list *is* the
supported-format list and there is nowhere else for the two to disagree.
"""

from __future__ import annotations

from app.documents.extraction import (  # noqa: F401
    email_eml,
    email_msg,
    office,
    pdf,
    text,
)
from app.documents.extraction.base import registry

__all__ = ["registry"]
