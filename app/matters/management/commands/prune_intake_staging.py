"""Delete `Uus teema` staging nobody will ask for again.

Two kinds of thing, and one answer for both: a session past its expiry is a
form somebody walked away from, and one a Matter has already consumed is
material that is now evidence somewhere else. Neither will ever be read again
(app/matters/intake_staging.py, docs/adr/0064).

**It cannot touch evidence.** It reads `MatterIntakeSession`, deletes objects
under the staging prefix of the held-uploads store, and deletes those rows. The
evidence store, the derivative store, `Document` and `DocumentVersion` are not
imported here and are not reachable from what is.

**Nothing schedules this.** It is a command an operator runs, and
`deploy/unraid-main/RECOVERY.md` says where the data lives and what losing it
costs. Putting a deletion loop into a production timer is a decision of its own
and is not made by writing one.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Delete expired and consumed Uus teema staging, and the files it held."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--limit",
            type=int,
            default=500,
            help="Maximum number of staging sessions to remove (default 500).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would go, and remove nothing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from app.matters.intake_staging import sweep_stale_sessions
        from app.matters.staging import MatterIntakeSession

        limit = options["limit"]
        stale = MatterIntakeSession.objects.stale()
        expired = stale.filter(consumed_at__isnull=True).count()
        consumed = stale.filter(consumed_at__isnull=False).count()
        self.stdout.write(
            f"Aegunud ettevalmistusi: {expired}. Juba kasutatud: {consumed}. Piir: {limit}."
        )

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Proovikäivitus — midagi ei kustutatud."))
            return

        report = sweep_stale_sessions(limit=limit)
        self.stdout.write(
            self.style.SUCCESS(
                f"Kustutatud {report.sessions} ettevalmistust, "
                f"{report.files} faili, {report.objects} objekti."
            )
        )
