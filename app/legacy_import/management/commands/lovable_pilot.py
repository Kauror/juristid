"""The Lovable pilot (docs/adr/0191).

    python manage.py lovable_pilot validate --package P
    python manage.py lovable_pilot plan     --package P
    python manage.py lovable_pilot dry-run  --package P
    python manage.py lovable_pilot apply    --package P \\
        --expect-package-sha256 D --expect-plan-sha256 E \\
        --operator-intent lovable-pilot --backup-set sets/YYYYMMDDTHHMMSSZ
    python manage.py lovable_pilot verify   --package P

`validate` checks the package with no database and prints its digest. `plan`
resolves every person, stage, instrument and organisation, checks that every
reference is free and prints the plan digest — no writes. `dry-run` writes the
whole package, verifies it record by record and rolls back; run it against an
isolated copy. `apply` writes it in one transaction, only with
``JURISTID_LOVABLE_PILOT`` and ``REAL_DATA_ALLOWED`` set, the typed intent, the
package digest, the reviewed plan digest (recomputed under a lock), a named
backup set and every reference free. Run again, it writes nothing. `verify`
reads back what was written and compares it with the package.

The package holds the Chamber's content: keep it outside the repository, CI and
every ordinary development database.
"""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.legacy_import import lovable_pilot


class Command(BaseCommand):
    help = "Validate, plan, dry-run, apply or verify a Lovable-pilot package (docs/adr/0191)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("mode", choices=["validate", "plan", "dry-run", "apply", "verify"])
        parser.add_argument("--package", required=True, help="The package file or directory.")
        parser.add_argument("--expect-package-sha256", default="")
        parser.add_argument(
            "--expect-plan-sha256",
            default="",
            help="apply: the plan digest `plan` printed and a person reviewed. Required.",
        )
        parser.add_argument("--operator-intent", default="")
        parser.add_argument(
            "--backup-set",
            default="",
            help="apply: the verified backup set taken before the import, sets/YYYYMMDDTHHMMSSZ.",
        )
        parser.add_argument("--json", action="store_true", help="Print a JSON summary.")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            getattr(self, "_" + options["mode"].replace("-", "_"))(options)
        except lovable_pilot.LovablePilotError as error:
            raise CommandError(str(error)) from error

    def _validate(self, options: dict[str, Any]) -> None:
        package = lovable_pilot.load_package(options["package"])
        report = lovable_pilot.validate_package(package)
        self._emit(
            options,
            {
                "package_sha256": lovable_pilot.package_digest(package),
                "topics": report.topics,
                "open_topics": report.open_topics,
                "steps": report.steps,
                "commencements_ready": report.commencements_ready,
                "commencements_source_only": report.commencements_source_only,
                "problems": report.problems,
            },
        )
        if report.problems:
            raise CommandError(f"{len(report.problems)} problem(s) in the package.")

    def _build(self, options: dict[str, Any]) -> lovable_pilot.LovablePlan:
        return lovable_pilot.build_plan(options["package"])

    def _summary(self, plan: lovable_pilot.LovablePlan) -> dict[str, Any]:
        return {
            "package_sha256": plan.digest,
            "plan_sha256": lovable_pilot.plan_digest(plan),
            "database_state": plan.database_state,
            "existing_matters": plan.existing_matters,
            "collisions": plan.collisions,
            "expected_writes": plan.expected_writes(),
            "reserve_through": plan.package.get("reserve_through") or {},
            "warnings": {t.reference: t.warnings for t in plan.topics if t.warnings},
            "problems": plan.problems,
        }

    def _plan(self, options: dict[str, Any]) -> None:
        plan = self._build(options)
        self._emit(options, self._summary(plan))
        if plan.problems:
            raise CommandError(f"{len(plan.problems)} problem(s) block the apply.")

    def _dry_run(self, options: dict[str, Any]) -> None:
        plan = self._build(options)
        report = lovable_pilot.dry_run(plan)
        verify = report.verify
        self._emit(
            options,
            {
                **self._summary(plan),
                "rolled_back": True,
                "written": report.written,
                "matters_before": report.matters_before,
                "matters_after_inside_transaction": report.matters_after,
                "existing_unchanged": report.fingerprint_before == report.fingerprint_after,
                "verified": verify.checked,
                "discrepancies": verify.discrepancies,
                "held": verify.held,
            },
        )
        if not verify.ok:
            raise CommandError(f"{len(verify.discrepancies)} discrepancy(ies) in the dry run.")

    def _apply(self, options: dict[str, Any]) -> None:
        plan = self._build(options)
        report = lovable_pilot.apply_plan(
            plan,
            operator_intent=options["operator_intent"],
            expect_package_sha256=options["expect_package_sha256"],
            expect_plan_sha256=options["expect_plan_sha256"],
            backup_set=options["backup_set"],
        )
        if report.already_applied:
            self.stdout.write(
                self.style.WARNING(
                    "Already applied: this package is in the database. Nothing was written."
                )
            )
            return
        self._emit(
            options,
            {
                "batch": str(report.batch_id),
                "written": report.written,
                "matters_before": report.matters_before,
                "matters_after": report.matters_after,
                "existing_unchanged": report.fingerprint_before == report.fingerprint_after,
            },
        )

    def _verify(self, options: dict[str, Any]) -> None:
        plan = self._build(options)
        report = lovable_pilot.verify_applied(plan)
        self._emit(
            options,
            {
                "database_state": plan.database_state,
                "checked": report.checked,
                "discrepancies": report.discrepancies,
                "held": report.held,
            },
        )
        if not report.ok:
            raise CommandError(f"{len(report.discrepancies)} discrepancy(ies).")

    def _emit(self, options: dict[str, Any], summary: dict[str, Any]) -> None:
        if options["json"]:
            self.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
            return
        for key, value in summary.items():
            self.stdout.write(f"{key}: {value}")
