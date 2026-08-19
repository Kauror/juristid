"""Drain the extraction queue once, with a bound.

The same work the worker does, in a form that finishes: a deployment step, a
cron entry, or a way to catch up after the worker was down. ``--limit`` is
required to have a value and defaults to something small, because a command that
silently starts a six-hour OCR run is a command nobody can use casually.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Process pending document extractions and exit."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--limit",
            type=int,
            default=25,
            help="Maximum number of versions to process (default 25).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from app.documents.extraction.orchestrator import (
            claim_version,
            extract_document_version,
            pending_versions,
        )

        limit = options["limit"]
        waiting = pending_versions().count()
        self.stdout.write(f"Ootel: {waiting} faili. Töötlen kuni {limit}.")

        processed = 0
        states: dict[str, int] = {}
        while processed < limit:
            candidate = pending_versions().first()
            if candidate is None:
                break
            claimed = claim_version(candidate.pk)
            if claimed is None:
                continue
            report = extract_document_version(claimed)
            states[report.state] = states.get(report.state, 0) + 1
            processed += 1

        summary = ", ".join(f"{state}: {count}" for state, count in sorted(states.items()))
        self.stdout.write(self.style.SUCCESS(f"Töödeldud {processed} faili. {summary}"))
        remaining = pending_versions().count()
        if remaining:
            # Said out loud rather than left for somebody to discover. A command
            # that stops at its limit and reports only what it did reads as
            # "everything is processed" (Stage-2B brief 48).
            self.stdout.write(f"Ootele jäi {remaining} faili.")
