"""Refresh the search projection for named Matters.

    python manage.py refresh_matter_search 2026_184 2025_12

The narrow counterpart to a full rebuild, for when one record changed and
reindexing everything would be silly. Same code path, same result.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.matters.models import Matter
from app.search.indexing import indexable_matters, refresh_matters


class Command(BaseCommand):
    help = "Refresh SearchDocument rows for one or more Matter references."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("references", nargs="*", help="Human references such as 2026_184.")
        parser.add_argument(
            "--all-open",
            action="store_true",
            help="Refresh every open Matter instead of naming references.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["all_open"]:
            queryset = indexable_matters().filter(is_open=True)
        else:
            references = options["references"]
            if not references:
                raise CommandError("Name at least one reference, or pass --all-open.")
            condition = None
            for reference in references:
                parsed = Matter.parse_reference(reference)
                if parsed is None:
                    raise CommandError(f"Not a reference: {reference!r}")
                year, number = parsed
                clause = {"reference_year": year, "reference_number": number}
                queryset_clause = indexable_matters().filter(**clause)
                condition = queryset_clause if condition is None else condition | queryset_clause
            queryset = condition if condition is not None else indexable_matters().none()

        count = refresh_matters(queryset)
        self.stdout.write(self.style.SUCCESS(f"Refreshed {count} matters."))
