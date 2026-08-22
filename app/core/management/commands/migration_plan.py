"""What `migrate` would do, read before it is run rather than after.

The deployment sequence applies migrations as a deliberate step, and a
deliberate step deserves something to look at. Without this the operator's only
options are to trust the release note or to read forty-nine migration files at
the moment they are least able to.

Reports, and does not migrate. Exit 0 means the report was produced, not that
the plan is safe — that judgement is the operator's, which is the point.

`--fail-on-consequential` turns it into a gate for a script: non-zero when the
plan contains an operation that removes or rewrites something, so an unattended
deployment stops and asks rather than proceeding on the assumption that every
migration is additive (docs/adr/0022).
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from app.core import deployment


class Command(BaseCommand):
    help = "Show the pending migration plan and flag operations that need a decision."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--fail-on-consequential",
            action="store_true",
            help=(
                "Exit non-zero when the plan is not purely additive. For a deployment "
                "script that must stop and ask a human."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        state = deployment.migration_state()

        if state.unknown:
            # Old code, new schema. Worth saying first: it changes what the
            # rest of the report means, because the database has already been
            # somewhere this code has never been.
            self.stdout.write(
                self.style.WARNING("The database has applied migrations this code does not have:")
            )
            for label in state.unknown:
                self.stdout.write(f"  {label}")
            self.stdout.write(
                "  This checkout is older than the database. Deploying it forward is a "
                "rollback across a schema change — see deploy/unraid-main/RECOVERY.md."
            )
            self.stdout.write("")

        if not state.pending:
            self.stdout.write(self.style.SUCCESS("No pending migrations."))
            return

        self.stdout.write(f"{len(state.pending)} pending migration(s):")
        for migration in state.pending:
            if migration.is_additive:
                self.stdout.write(f"  {migration.label}  (additive)")
                continue
            self.stdout.write(self.style.WARNING(f"  {migration.label}"))
            for operation, why in sorted(migration.consequential.items()):
                self.stdout.write(f"      {operation}: {why}")

        consequential = state.consequential
        self.stdout.write("")
        if not consequential:
            self.stdout.write(
                self.style.SUCCESS(
                    "Every pending migration is additive, so the release now serving "
                    "keeps working against the new schema while it is replaced."
                )
            )
            return

        message = (
            f"{len(consequential)} pending migration(s) remove or rewrite something. "
            "Take a backup first and decide whether the release now serving survives "
            "the new schema; if it does not, the deployment needs an announced "
            "maintenance window rather than a rolling replacement."
        )
        if options["fail_on_consequential"]:
            raise CommandError(message)
        self.stdout.write(self.style.WARNING(message))
