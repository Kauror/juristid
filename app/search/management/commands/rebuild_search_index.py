"""Rebuild the whole search projection from canonical records.

    python manage.py rebuild_search_index

**The rebuild is atomic.** Search keeps serving the previous complete index for
the whole run, and the new one appears only when the entire rebuild has
succeeded. If the command is interrupted or fails partway, the old index is
still there and still complete.

That guarantee is the reason to prefer this command over ad-hoc reindexing, and
it is worth being precise about why it was needed. Being derived data makes an
index cheap to recreate; it does not make a half-built one safe to serve. A
partially rebuilt index answers confidently and silently with a fraction of the
corpus, and "vasteid ei leitud" looks identical whether a matter does not exist
or the rebuild died before reaching it.

Run it after bulk changes that the per-write signals do not cover — renaming an
Organisation, editing its aliases, merging a Tag — or whenever the index is
suspect.

It rebuilds document fragments too, but it does **not** re-extract them: it
projects the derivatives that already exist. If the derived text itself is
suspect, `rebuild_document_derivatives` is the command, and it reindexes what it
rebuilds as it goes.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from app.search.indexing import BATCH_SIZE, rebuild_all


class Command(BaseCommand):
    help = "Rebuild the SearchDocument projection from scratch, atomically."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--batch-size",
            type=int,
            default=BATCH_SIZE,
            help="Rows per statement batch. Bounds memory; the whole rebuild is "
            "still one transaction.",
        )
        parser.add_argument(
            "--keep-existing",
            action="store_true",
            help="Refresh in place instead of emptying first. Still atomic, and "
            "every source that currently exists converges — but rows whose "
            "source no longer qualifies survive: a deleted Matter, a deleted "
            "entry, a page of a derivative that has since been superseded. "
            "Use it to refresh without a gap; use the default to clean up.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        result = rebuild_all(batch_size=options["batch_size"], clear=not options["keep_existing"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Indexed {result.matters} matters, {result.entries} entries, "
                f"{result.submissions} submissions and {result.fragments} document "
                f"fragments into {result.documents} rows in {result.seconds:.2f}s "
                f"(index version {result.index_version})."
            )
        )
