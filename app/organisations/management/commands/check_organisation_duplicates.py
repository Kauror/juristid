"""Which institutions does the organisation key call one?

    python manage.py check_organisation_duplicates

**Read-only, always.** It lists the rows a person would have to look at and
merges nothing: which of two same-named rows is the ministry is a question
about the filing each one carries, and a management command is not the place
to answer it (ENG-045, `app/organisations/duplicates.py`).

Three findings — institutions colliding by name, by alias, and rows whose stored
matching key predates the current fold — each with the institution's id, its
name with invisible characters written out, and how many records point at it.
Organisation names are reference data rather than case material, so printing
them is safe; nothing from a Matter is read or printed.

Exit 0 when nothing was found, 1 when something was, so it composes with a
cron job or a deployment step like `check_evidence_integrity`.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from app.organisations.duplicates import Collision, identity_report, visible


class Command(BaseCommand):
    help = (
        "List Organisations that the organisation matching key calls one institution, "
        "by name and by alias, and stored keys the current key would not produce. Read-only."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        report = identity_report()

        self.stdout.write(f"Organisations checked: {report.organisations}")
        self.stdout.write(f"Aliases checked:       {report.aliases}")

        if report.ok:
            self.stdout.write(self.style.SUCCESS("No colliding organisations found."))
            return

        self._collisions("Colliding by name", report.by_name)
        self._collisions("Colliding by alias", report.by_alias)

        if report.stale:
            self.stdout.write(
                self.style.WARNING(
                    f"\nStale key: {len(report.stale)} row(s) whose stored matching key the "
                    "current key would not produce"
                )
            )
            for row in report.stale:
                self.stdout.write(
                    f"  {row.kind} {row.pk}\t{visible(row.text)}\t"
                    f"stored {visible(row.stored)!r}, current {row.expected!r}"
                )

        self.stdout.write(
            self.style.ERROR(
                "\nNothing was changed. Merging is a decision about the filing each row "
                "carries; make it with the rows above in front of you. Stale keys are "
                "recomputed by the organisations migration and on the row's next save."
            )
        )
        raise SystemExit(1)

    def _collisions(self, title: str, collisions: list[Collision]) -> None:
        if not collisions:
            return
        self.stdout.write(self.style.ERROR(f"\n{title}: {len(collisions)} group(s)"))
        for collision in collisions:
            self.stdout.write(
                f"  key {collision.key!r} — {len(collision.organisations)} organisations"
            )
            for organisation in collision.organisations:
                detail = ", ".join(
                    f"{label} {count}" for label, count in sorted(organisation.references.items())
                )
                self.stdout.write(
                    f"    {organisation.pk}\t{visible(organisation.name)}\t"
                    f"references: {organisation.reference_total}"
                    + (f" ({detail})" if detail else "")
                )
