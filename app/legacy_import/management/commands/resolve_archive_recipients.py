"""Attach historical recipients that the reference data can now resolve.

    python manage.py resolve_archive_recipients
    python manage.py resolve_archive_recipients --apply --expect-plan-sha256 D
    python manage.py resolve_archive_recipients --mappings reviewed-aliases.toml \\
        --apply --expect-plan-sha256 D

``--apply`` writes only the pass a person reviewed: the recipient and
organisation tables are held, the pass is decided again inside the writing
transaction, and it must hash to the digest the read-only run printed — the
alias file's bytes and every attachment (docs/adr/0184).

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
            "--expect-plan-sha256",
            default="",
            help="apply: the plan digest the read-only run printed and a person reviewed.",
        )
        parser.add_argument(
            "--show",
            type=int,
            default=25,
            help="How many unresolved source strings to list (0 for all).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from django.db import transaction

        from app.legacy_import.integrity import IntegrityError, hold_tables, require_digest
        from app.legacy_import.opinion_recipients import (
            apply_recipient_plan,
            recipient_plan_digest,
            resolve_recipients,
        )
        from app.organisations.models import Organisation, OrganisationAlias
        from app.submissions.models import SubmissionRecipient

        if not options["apply"]:
            report = resolve_recipients(apply=False, mappings=self._mappings(options))
            digest = recipient_plan_digest(report)
        else:
            with transaction.atomic():
                # Held before deciding: an alias or a recipient added meanwhile
                # would change what the reviewed pass attaches.
                hold_tables(SubmissionRecipient, Organisation, OrganisationAlias)
                report = resolve_recipients(apply=False, mappings=self._mappings(options))
                digest = recipient_plan_digest(report)
                try:
                    require_digest(
                        digest,
                        options["expect_plan_sha256"],
                        flag="--expect-plan-sha256",
                        what="The recipient resolution plan",
                    )
                except IntegrityError as error:
                    raise CommandError(str(error)) from error
                apply_recipient_plan(report)

        write = self.stdout.write
        write("")
        write(self.style.MIGRATE_HEADING("Arhiivi saajate lahendamine"))
        write(f"  vaadatud päritolukirjeid  {report.examined}")
        write(f"  juba seotud               {report.already_attached}")
        write(f"  lahendus leitud           {report.resolved}")
        write(f"  endiselt lahendamata      {report.still_unresolved}")
        write(f"  plan sha256 {digest}")

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
            write(f"plan-sha256 {digest}")
        else:
            write(self.style.SUCCESS("Ainult arvutus: andmebaasi ei kirjutatud."))

    def _mappings(self, options: dict[str, Any]) -> Any:
        from app.legacy_import.resolution import MappingTables

        if not options["mappings"]:
            return None
        try:
            return MappingTables.load(options["mappings"])
        except Exception as error:
            raise CommandError(f"Vastendusfaili ei saanud lugeda: {error}") from error
