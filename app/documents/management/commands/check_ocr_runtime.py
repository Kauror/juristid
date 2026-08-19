"""Prove the OCR engine this deployment claims to have is really there.

Two failures this exists to make loud.

**The engine is missing.** Extraction then silently downgrades: scanned PDFs
land in FAILED with a message about OCR, images the same, and the corpus quietly
loses everything that arrived as a photograph. Nothing errors, so nobody looks.

**A language is missing.** Worse, because it is invisible. Tesseract asked for a
language it does not have falls back to English and returns confident nonsense
for Estonian text — which reaches the search index looking exactly like a
successful extraction, and is indistinguishable from real text until somebody
tries to quote it.

Run in CI after installing the packages, and on the server after a deployment.
Exits non-zero when the runtime is not what it should be.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Verify that the OCR engine and its language data are installed."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--required",
            action="store_true",
            help="Fail when OCR is disabled by configuration, rather than reporting it.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from app.documents.extraction.ocr import (
            OcrUnavailable,
            configured_languages,
            missing_languages,
            ocr_engine_version,
        )

        if not settings.EXTRACTION_OCR_ENABLED:
            message = "OCR is switched off (EXTRACTION_OCR_ENABLED=0)."
            if options["required"]:
                raise CommandError(message)
            self.stdout.write(self.style.WARNING(message))
            return

        try:
            version = ocr_engine_version()
        except OcrUnavailable as error:
            raise CommandError(
                f"OCR is enabled but the engine is not usable: {error}. "
                "Install tesseract-ocr and its language data."
            ) from error

        missing = missing_languages()
        if missing:
            raise CommandError(
                f"{version} is installed but is missing language data for "
                f"{', '.join(missing)}. Estonian text would be recognised with the "
                "wrong model and the result would look like a successful extraction."
            )

        languages = "+".join(configured_languages())
        self.stdout.write(self.style.SUCCESS(f"{version}, languages {languages}"))
