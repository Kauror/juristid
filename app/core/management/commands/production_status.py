"""Is the running application internally healthy? One screen, one exit code.

    python manage.py production_status
    python manage.py production_status --json
    python manage.py production_status --detail

A convenience roll-up over read-only checks that already exist, added because
the deployment procedure asks an operator to run five of them one after another
and five exit codes read one at a time is where one gets skipped. It is
**optional**: the individual commands remain the ones the runbook cites and the
ones that say more than a PASS ever can, and nothing here replaces release
artifact verification, the target-image `migration_plan`, the pre-deploy backup,
the container and `/healthz` checks, or the post-deploy browser pass
(docs/production-readiness.md).

Reports. Never repairs — see `app/core/production_status.py`, which is also
where the checks left out of the default are listed with the reason.
"""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from app.core import production_status as status_module


class Command(BaseCommand):
    help = "Summarise the current application and database read-only checks. Changes nothing."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Emit the same verdicts as JSON. The exit code is unchanged.",
        )
        parser.add_argument(
            "--detail",
            action="store_true",
            help="Print each failing check's aggregate detail under its row.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        report = status_module.production_status()

        if options["as_json"]:
            self.stdout.write(json.dumps(status_module.as_dict(report), indent=2, sort_keys=True))
        else:
            self.stdout.write("PRODUCTION STATUS")
            self.stdout.write("")
            for line in status_module.render(report):
                self.stdout.write(line)
            if options["detail"]:
                self._write_detail(report)
            if not report.ok:
                self.stdout.write("")
                self.stdout.write(
                    "A failing row is a report, not an instruction that has been carried out. "
                    "Run that check's own command for the whole picture, and its own documented "
                    "remedy separately."
                )

        # Non-zero on any failure, and zero only when every included check
        # passed. `SystemExit` rather than `CommandError`: the failure is the
        # answer to the question that was asked, not a command that went wrong,
        # and a traceback-shaped stderr would read as the latter.
        if not report.ok:
            raise SystemExit(1)

    def _write_detail(self, report: status_module.StatusReport) -> None:
        failing = [check for check in report.checks if not check.ok and check.detail]
        if not failing:
            return
        self.stdout.write("")
        for check in failing:
            self.stdout.write(f"  {check.label}: {check.detail}")
