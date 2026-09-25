"""Rebuild the whole search projection from canonical records.

    python manage.py rebuild_search_index

**Readers never see a partial index.** Search keeps serving the previous
complete generation for the whole run; the new one is built beside it and
becomes the one readers read only when the entire rebuild has succeeded. If the
command is interrupted or fails partway, the old generation is still there,
still complete and still the one in use, and the next run cleans up what this
one left (docs/adr/0118).

**Business writes carry on.** The rebuild holds the refresh gate one batch at
a time, not for the whole run, so a save that refreshes search waits for at
most one batch — the command prints the longest such hold, and the swap's.
Only one rebuild runs at a time; a second one refuses rather than waits.

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

from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.search.freshness import rebuild_and_discharge
from app.search.indexing import BATCH_SIZE, RebuildAlreadyRunning


class Command(BaseCommand):
    help = "Rebuild the SearchDocument projection from scratch, atomically."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--batch-size",
            type=int,
            default=BATCH_SIZE,
            help="Sources per batch. Each batch is one short transaction under the "
            "refresh gate, so this also bounds how long a concurrent save can wait.",
        )
        parser.add_argument(
            "--keep-existing",
            action="store_true",
            help="Accepted for existing scripts; changes nothing. Every rebuild now "
            "builds a new generation beside the one in use, so there is no gap to "
            "avoid and nothing stale survives either way.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # Through the same claim-rebuild-discharge path as the worker, so a
        # successful manual repair also clears the debt it repaired and the
        # freshness healthcheck goes green with the index (ENG-085).
        try:
            outcome = rebuild_and_discharge(
                batch_size=options["batch_size"], clear=not options["keep_existing"]
            )
        except RebuildAlreadyRunning as error:
            raise CommandError(str(error)) from error
        result = outcome.result
        if result is None:  # pragma: no cover - a rebuild that returns has a result
            raise CommandError("The rebuild reported no result.")
        self.stdout.write(
            self.style.SUCCESS(
                f"Indexed {result.matters} matters, {result.entries} entries, "
                f"{result.submissions} submissions, {result.document_rows} documents and "
                f"{result.fragments} document fragments into {result.documents} rows in "
                f"{result.seconds:.2f}s "
                f"(index version {result.index_version}, generation {result.generation})."
            )
        )
        self.stdout.write(
            f"Refresh gate held at most {result.longest_gate_seconds:.3f}s per batch "
            f"over {result.batches} batches; the swap held it {result.swap_gate_seconds:.3f}s."
        )
        if outcome.cleared:
            self.stdout.write(
                f"Cleared {outcome.cleared} outstanding rebuild obligation(s) this rebuild covered."
            )
