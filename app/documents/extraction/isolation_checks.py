"""System checks for the parser boundary's settings (docs/adr/0230)."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.checks import Error, register


@register()
def check_parser_isolation(app_configs: Any = None, **kwargs: Any) -> list[Error]:
    from app.documents.extraction.isolation import MODES, SERVICE

    errors: list[Error] = []
    chosen = str(settings.DOCUMENT_PARSER_ISOLATION).strip().lower()
    if chosen not in MODES:
        errors.append(
            Error(
                f"DOCUMENT_PARSER_ISOLATION={chosen!r} is not one of {sorted(MODES)}.",
                id="documents.E001",
            )
        )
    if chosen == SERVICE and not settings.DOCUMENT_PARSER_SOCKET:
        errors.append(
            Error(
                "DOCUMENT_PARSER_ISOLATION=service needs DOCUMENT_PARSER_SOCKET.",
                id="documents.E001",
            )
        )
    # The longest an intake parse can hold its claim: its own deadline, the wait
    # for the file in front of it at the service, and the hand-back. Past the
    # reader's stale-claim window a second reader would take the row while the
    # first is still parsing it (docs/adr/0230 §Limits).
    held = (
        settings.DOCUMENT_PARSER_INTAKE_WALL_SECONDS + settings.DOCUMENT_PARSER_QUEUE_SECONDS + 15
    )
    window = settings.INTAKE_READER_STALE_CLAIM_MINUTES * 60
    if held >= window:
        errors.append(
            Error(
                f"An intake parse can hold its claim {held} s, not less than the "
                f"{window} s stale-claim window.",
                hint="Lower DOCUMENT_PARSER_INTAKE_WALL_SECONDS or "
                "DOCUMENT_PARSER_QUEUE_SECONDS, or raise INTAKE_READER_STALE_CLAIM_MINUTES.",
                id="documents.E001",
            )
        )
    batch = settings.DOCUMENT_PARSER_BATCH_WALL_SECONDS + settings.DOCUMENT_PARSER_QUEUE_SECONDS
    if batch + 15 >= settings.EXTRACTION_STALE_CLAIM_MINUTES * 60:
        errors.append(
            Error(
                "DOCUMENT_PARSER_BATCH_WALL_SECONDS outlasts EXTRACTION_STALE_CLAIM_MINUTES.",
                id="documents.E001",
            )
        )
    return errors
