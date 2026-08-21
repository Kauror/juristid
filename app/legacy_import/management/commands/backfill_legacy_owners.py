"""Restore ``VASTUTAJA`` on Matters that imported without an owner.

    python manage.py backfill_legacy_owners --dry-run
    python manage.py backfill_legacy_owners --apply

There is no default mode, for the same reason the import has none: the safe
guess is the one people stop reading. Both modes read ownership out of the
provenance already stored — no workbook is opened and no source byte is touched.

The output is aggregate. The distinct source values nobody could identify are
useful to whoever writes the mapping file and are source content, so they are
written only on request and only into ignored local storage.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.legacy_import.contracts import ContractError
from app.legacy_import.owner_backfill import (
    Outcome,
    apply_backfill_plan,
    build_backfill_plan,
    summary,
)
from app.legacy_import.resolution import (
    METHOD_EXACT,
    METHOD_GIVEN_NAME,
    METHOD_MAPPING,
    MappingFileError,
    MappingTables,
)

#: Directories .gitignore keeps out of the repository. A file of real register
#: names must land in one of them, or outside the checkout entirely.
LOCAL_ONLY_DIRECTORIES: tuple[str, ...] = ("private-data", "import-input", "import-output")

_OUTCOME_LABELS: dict[str, str] = {
    Outcome.WOULD_ASSIGN: "deterministically resolvable (would update)",
    Outcome.ALREADY_OWNED: "already owned (untouched)",
    Outcome.NO_SOURCE_OWNER: "blank source owner",
    Outcome.CONFLICTING_SOURCES: "conflicting source observations",
    Outcome.AMBIGUOUS: "ambiguous",
    Outcome.MULTI_PERSON: "multi-person",
    Outcome.UNKNOWN_OWNER_VALUE: "unknown owner values",
    Outcome.NO_CONTRACT: "no readable era contract",
}

_METHOD_LABELS: dict[str, str] = {
    METHOD_MAPPING: "mapping-resolved",
    METHOD_EXACT: "full-name-resolved",
    METHOD_GIVEN_NAME: "given-name-resolved",
}


class Command(BaseCommand):
    help = "Fill in missing Matter owners from the imported register's own provenance."

    def add_arguments(self, parser: CommandParser) -> None:
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="Read the database, decide nothing, write nothing.",
        )
        mode.add_argument(
            "--apply",
            action="store_true",
            help="Assign the owners the plan found. Idempotent; safe to repeat.",
        )
        parser.add_argument(
            "--mapping-file",
            default=None,
            help="Reviewed owner mappings (TOML or JSON). The same file the importer takes.",
        )
        parser.add_argument(
            "--unresolved-file",
            default=None,
            help=(
                "Write the distinct unidentified owner values to this CSV. Source "
                "content: the path must be inside ignored local storage."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            mappings = MappingTables.load(options["mapping_file"])
        except MappingFileError as error:
            raise CommandError(str(error)) from error

        unresolved_path = self._checked_unresolved_path(options["unresolved_file"])

        try:
            plan = build_backfill_plan(mappings=mappings)
        except (ContractError, MappingFileError) as error:
            raise CommandError(str(error)) from error

        self._report(plan)

        if unresolved_path is not None:
            self._write_unresolved(plan, unresolved_path)

        if options["dry_run"]:
            self.stdout.write("")
            self.stdout.write("Dry run: nothing was written to the database.")
            return

        result = apply_backfill_plan(plan)
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Applied"))
        self.stdout.write(f"  owners assigned  {result.assigned}")
        self.stdout.write(f"  matters examined {result.examined}")

    # -- output ------------------------------------------------------------

    def _report(self, plan: Any) -> None:
        figures = summary(plan)
        self.stdout.write(self.style.MIGRATE_HEADING("Owner backfill"))
        self.stdout.write(f"  operation version {figures['operation_version']}")
        self.stdout.write(f"  matters examined  {figures['matters_examined']}")
        self.stdout.write("")
        for outcome, count in figures["outcomes"].items():
            self.stdout.write(f"  {_OUTCOME_LABELS[outcome]:<42} {count}")
        self.stdout.write("")
        for method, count in figures["methods"].items():
            self.stdout.write(f"  {_METHOD_LABELS[method]:<42} {count}")
        self.stdout.write("")
        self.stdout.write(f"  {'would update':<42} {figures['would_update']}")
        self.stdout.write(
            f"  {'distinct unidentified values':<42} {figures['distinct_unresolved_values']}"
        )

    def _checked_unresolved_path(self, raw: str | None) -> Path | None:
        """Refuse to write register names anywhere the repository can see.

        A report of unresolved owner cells is a list of colleagues' names. The
        .gitignore already keeps three directories out of Git; this makes the
        command refuse rather than rely on nobody running ``git add -A``
        afterwards (Stage-2F brief 10, 50).
        """
        if not raw:
            return None
        path = Path(raw).resolve()
        repository = Path(settings.BASE_DIR).resolve()
        if not path.is_relative_to(repository):
            return path
        first = path.relative_to(repository).parts[0]
        if first not in LOCAL_ONLY_DIRECTORIES:
            raise CommandError(
                f"{path} is inside the repository but not in ignored local storage. Use one of "
                f"{', '.join(LOCAL_ONLY_DIRECTORIES)}, or a path outside the checkout."
            )
        return path

    def _write_unresolved(self, plan: Any, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # utf-8-sig and semicolons, because the person reviewing this opens it
        # in Excel in Tallinn, where plain UTF-8 and commas both read wrongly.
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(["vastutaja allikas", "esinemisi"])
            writer.writerows(sorted(plan.unresolved_values.items()))
        self.stdout.write("")
        self.stdout.write(f"Unidentified owner values written to {path}")
        self.stdout.write("This file contains source content and stays in local storage.")
