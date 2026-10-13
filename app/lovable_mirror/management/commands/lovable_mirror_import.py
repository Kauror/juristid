"""Import one Lovable export into the mirror tables. Writes nothing canonical.

    python manage.py lovable_mirror_import --export DIR
    python manage.py lovable_mirror_import --export DIR --expect-sha256 SHA
    python manage.py lovable_mirror_import --live        # refuses: no reader yet

``DIR`` is an operator-produced bundle (``manifest.json`` + one JSON file per
table; docs/lovable-mirror.md §"Export format") **outside the checkout**, in an
approved real-data environment. A bundle marked ``REAL`` is refused unless the
deployment has ``REAL_DATA_ALLOWED``. Importing the same bundle twice changes
nothing the second time.

``--live`` is the seam for the future read-only refresh. It refuses in every
configuration today and makes no connection (app/lovable_mirror/readers.py).
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.lovable_mirror.importer import import_from
from app.lovable_mirror.readers import (
    BundleRefused,
    FileExportReader,
    LiveReadOnlyReader,
    ReaderUnavailable,
    SourceReader,
)


class Command(BaseCommand):
    help = "Import a Lovable export bundle into the read-only mirror tables."

    def add_arguments(self, parser: CommandParser) -> None:
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--export", help="Bundle directory outside the repository.")
        source.add_argument(
            "--live",
            action="store_true",
            help="Read live from Lovable. Disabled: no authorised reader exists.",
        )
        parser.add_argument(
            "--expect-sha256",
            default="",
            help="Refuse unless manifest.json hashes to exactly this digest.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        reader: SourceReader
        if options["live"]:
            reader = LiveReadOnlyReader()
        else:
            reader = FileExportReader(options["export"], expected_sha256=options["expect_sha256"])
        try:
            result = import_from(reader)
        except (BundleRefused, ReaderUnavailable) as error:
            raise CommandError(str(error)) from error

        snapshot = result.snapshot
        if result.created:
            self.stdout.write(self.style.SUCCESS("Hetktõmmis imporditud."))
        else:
            self.stdout.write(self.style.WARNING("Sama kontrollsummaga hetktõmmis on juba olemas."))
        self.stdout.write(f"  SHA-256        {snapshot.manifest_sha256}")
        self.stdout.write(f"  eksporditud    {snapshot.source_exported_at.isoformat()}")
        self.stdout.write(f"  skeem          {snapshot.source_schema_version}")
        for name, count in sorted(snapshot.row_counts.items()):
            self.stdout.write(f"  {name:<16} {count}")
        for name, fields in sorted(snapshot.uncatalogued_fields.items()):
            self.stdout.write(self.style.WARNING(f"  kataloogimata {name}: {', '.join(fields)}"))
        self.stdout.write(
            self.style.SUCCESS(
                "Kirjutati ainult peegli tabelitesse; kanoonilisi andmeid ei muudetud."
            )
        )
