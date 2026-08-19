"""Rebuild the whole search projection from canonical records.

    python manage.py rebuild_search_index

Safe to run at any time and safe to interrupt: the projection is derived data,
so the worst outcome of a half-finished rebuild is a stale index, which the next
run corrects. Nothing in the domain reads from it.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from app.search.indexing import BATCH_SIZE, rebuild_all


class Command(BaseCommand):
    help = "Rebuild the SearchDocument projection from scratch."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
        parser.add_argument(
            "--keep-existing",
            action="store_true",
            help="Refresh in place instead of emptying first. Leaves orphaned documents.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        result = rebuild_all(batch_size=options["batch_size"], clear=not options["keep_existing"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Indexed {result.matters} matters into {result.documents} documents "
                f"in {result.seconds:.2f}s (index version {result.index_version})."
            )
        )
