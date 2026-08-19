"""Content extraction: bytes in, derived text and metadata out.

The public surface is deliberately small. Callers ask for
:func:`extract_document_version` and get a report; everything about which parser
handled it, how it was claimed and what got written is internal.

Nothing here is imported at Django start-up. The parser libraries are heavy and
only the worker and the extraction commands need them, so the app's web process
never pays for a PDF renderer it will not use.
"""

from __future__ import annotations

from app.documents.extraction.errors import (
    ExtractionError,
    ExtractionFailed,
    ExtractionNotApplicable,
)

__all__ = [
    "ExtractionError",
    "ExtractionFailed",
    "ExtractionNotApplicable",
    "extract_document_version",
    "supported_mime_types",
]


def extract_document_version(*args: object, **kwargs: object) -> object:
    from app.documents.extraction.orchestrator import extract_document_version as run

    return run(*args, **kwargs)  # type: ignore[arg-type]


def supported_mime_types() -> frozenset[str]:
    from app.documents.extraction.parsers import registry

    return registry.supported_mime_types()
