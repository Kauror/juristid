"""Attach historical recipients that the reference data can now resolve.

    python manage.py resolve_archive_recipients
    python manage.py resolve_archive_recipients --apply
    python manage.py resolve_archive_recipients --mappings reviewed-aliases.toml --apply

A backfill, deliberately separate from the apply that created the Submissions.

The archive knows who Koda wrote to, as a string. On the day a letter is filed
that string very often resolves to no canonical ``Organisation`` — the body has
been renamed, merged, or written in an abbreviation nobody has reviewed yet — and
until now that was permanent. The apply attached no recipient and returned; the
*next* apply found the provenance row for that occurrence and stopped before it
ever reached the recipient again. Improving the reference data changed nothing,
and nothing said so.

This command exists so that improving the reference data is worth doing. It runs
whenever an operator has added a reviewed alias or an Organisation, attaches what
now resolves exactly, and leaves the rest for the next time.

**Read-only unless ``--apply``.** Without it, nothing is written and the report
is the operator's work list: the distinct unresolved strings and how many letters
each one is holding up, which is the number that says which alias to review next.

It never creates an Organisation, never creates a Submission, and never rewrites
``recipient_raw``. A historical spelling is evidence that somebody was written
to, not evidence that a body exists under that name today.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser


class Command(BaseCommand):
    help = "Attach historical archive recipients that now resolve exactly. Idempotent."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help=(
                "Write the recipients. Without it the command decides everything "
                "and writes nothing."
            ),
        )
        parser.add_argument(
            "--mappings",
            type=Path,
            default=None,
            help="A reviewed alias file. Without it only exact identity resolves.",
        )
        parser.add_argument(
            "--show",
            type=int,
            default=25,
            help="How many unresolved source strings to list (0 for all).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from app.legacy_import.opinion_recipients import resolve_recipients
        from app.legacy_import.resolution import MappingTables

        mappings = None
        if options["mappings"]:
            try:
                mappings = MappingTables.load(options["mappings"])
            except Exception as error:
                raise CommandError(f"Vastendusfaili ei saanud lugeda: {error}") from error

        report = resolve_recipients(apply=bool(options["apply"]), mappings=mappings)

        write = self.stdout.write
        write("")
        write(self.style.MIGRATE_HEADING("Arhiivi saajate lahendamine"))
        write(f"  vaadatud päritolukirjeid  {report.examined}")
        write(f"  juba seotud               {report.already_attached}")
        write(f"  lahendus leitud           {report.resolved}")
        write(f"  endiselt lahendamata      {report.still_unresolved}")

        if report.unresolved_values:
            write("")
            write(self.style.MIGRATE_HEADING("Lahendamata allikaväärtused"))
            # Most-blocking first: this is the operator's work list, and the
            # useful ordering is "which reviewed alias unblocks the most
            # letters", not alphabetical.
            rows = sorted(report.unresolved_values.items(), key=lambda kv: (-kv[1], kv[0]))
            limit = options["show"] or len(rows)
            for value, count in rows[:limit]:
                write(f"  {count:>4}  {value}")
            if len(rows) > limit:
                write(f"  … veel {len(rows) - limit} väärtust")

        for note in report.notes:
            write("")
            write(self.style.WARNING(note))

        write("")
        if options["apply"]:
            write(self.style.SUCCESS(f"Seotud {report.resolved} saajat. Ajalugu ei muudetud."))
        else:
            write(self.style.SUCCESS("Ainult arvutus: andmebaasi ei kirjutatud."))
