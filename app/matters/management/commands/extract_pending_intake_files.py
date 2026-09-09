"""Read the files staged on `Uus teema`, and exit.

The staged half of `extract_pending_documents`: the same parsers, the same scan
gate, a different publication target. A file chosen while a Teema is being
created has no ``DocumentVersion`` to hang derivatives on, so what comes out of
the parser is written as JSON on the staged row and read by exactly one caller
— the analyser that fills the form somebody is still looking at
(app/matters/intake_extraction.py, docs/adr/0064).

Its own command rather than a branch of the canonical one, deliberately. The
two queues hold different tables with different lifetimes, and an operator
draining the evidence backlog after a worker outage should not have to reason
about somebody's half-finished form. The long-running `run_extraction_worker`
drains both, so a deployment still runs one process.

``--limit`` has a small default for the reason it does on the canonical
command: a command that silently starts a long OCR run is a command nobody can
use casually.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Read the files staged on Uus teema and exit."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--limit",
            type=int,
            default=25,
            help="Maximum number of staged files to read (default 25).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from app.matters.intake_extraction import drain, pending_intake_files

        limit = options["limit"]
        waiting = pending_intake_files().count()
        self.stdout.write(f"Ootel: {waiting} ettevalmistatud faili. Loen kuni {limit}.")

        reports = drain(limit=limit)
        states: dict[str, int] = {}
        for report in reports:
            states[report.state] = states.get(report.state, 0) + 1
            # Named rather than left in the summary count. "3 files, 1 failed"
            # sends an operator to the database to find out which; the answer
            # costs one line and no document content.
            if report.error_code:
                self.stdout.write(
                    self.style.WARNING(f"  {report.error_code} — {report.note[:120]}")
                )

        summary = ", ".join(f"{state}: {count}" for state, count in sorted(states.items()))
        self.stdout.write(self.style.SUCCESS(f"Loetud {len(reports)} faili. {summary}".strip()))
