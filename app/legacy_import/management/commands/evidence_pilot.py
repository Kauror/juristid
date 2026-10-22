"""The evidence pilot (docs/adr/0173).

    python manage.py evidence_pilot validate --package P
    python manage.py evidence_pilot plan     --package P
    python manage.py evidence_pilot apply    --package P \\
        --expect-package-sha256 D --expect-plan-sha256 E \\
        --operator-intent evidence-pilot --backup-set SET
    python manage.py evidence_pilot verify   --package P

`validate` checks the package and re-hashes every file it points at — no
database at all — and prints the package manifest's digest (every file of the
package, docs/adr/0184). `plan` resolves every person, stage, instrument and
organisation, says what the database holds and prints the plan digest — no
writes. `apply` writes the package in one transaction, only with
``JURISTID_EVIDENCE_PILOT`` and ``REAL_DATA_ALLOWED`` set, the typed intent, the
package's own digest, the reviewed plan digest (recomputed inside the
transaction, over the package re-hashed file by file), a named backup set and a
database holding no business Matter the package did not create. Run again with
the same package, it writes nothing. `verify` reads back what was written and
compares it with the package.

``--manifest-out FILE`` (validate, plan) writes the manifest as
``path<TAB>size<TAB>sha256`` lines for the operator's record. Never write it
into the package: the package is what the manifest describes.

The package holds the Chamber's and its members' content: keep it outside the
repository, CI and every ordinary development database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.legacy_import import evidence_pilot
from app.legacy_import.integrity import IntegrityError
from app.legacy_import.limits import SourceLimitExceeded


class Command(BaseCommand):
    help = "Validate, plan, apply or verify an evidence-pilot package (docs/adr/0173)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("mode", choices=["validate", "plan", "apply", "verify"])
        parser.add_argument("--package", required=True, help="The package directory.")
        parser.add_argument("--expect-package-sha256", default="")
        parser.add_argument(
            "--expect-plan-sha256",
            default="",
            help="apply: the plan digest `plan` printed and a person reviewed. Required.",
        )
        parser.add_argument(
            "--manifest-out",
            default="",
            help="validate, plan: write the package manifest here (outside the package).",
        )
        parser.add_argument("--operator-intent", default="")
        parser.add_argument(
            "--backup-set",
            default="",
            help="apply: the verified backup set taken before the reset, e.g. sets/20261019T…Z.",
        )
        parser.add_argument("--json", action="store_true", help="Print a JSON summary.")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            getattr(self, f"_{options['mode']}")(options)
        except evidence_pilot.EvidencePilotError as error:
            raise CommandError(str(error)) from error

    def _write_manifest(self, options: dict[str, Any], manifest: Any) -> None:
        target = (options.get("manifest_out") or "").strip()
        if not target:
            return
        path = Path(target).resolve()
        package = Path(options["package"]).resolve()
        if path == package or package in path.parents:
            raise CommandError("Write the manifest outside the package it describes.")
        path.write_text(manifest.as_tsv(), encoding="utf-8", newline="\n")

    def _validate(self, options: dict[str, Any]) -> None:
        manifest = evidence_pilot.package_manifest(options["package"])
        package, archive = evidence_pilot.load_package(options["package"], manifest=manifest)
        try:
            report = evidence_pilot.validate_package(package, archive)
        except (IntegrityError, SourceLimitExceeded) as error:
            raise CommandError(str(error)) from error
        digest = evidence_pilot.package_digest(package)
        self._write_manifest(options, manifest)
        summary = {
            "package_sha256": digest,
            "manifest_sha256": manifest.digest(),
            "manifest_files": len(manifest),
            "topics": report.topics,
            "records_ready": report.records_ready,
            "records_held": report.records_held,
            "documents_ready": report.documents_ready,
            "documents_held": report.documents_held,
            "resources_on_pages": report.resources_on_pages,
            "resources_accounted": report.resources_accounted,
            "problems": report.problems,
            "warnings": report.warnings,
        }
        self._emit(options, summary)
        if report.problems:
            raise CommandError(f"{len(report.problems)} problem(s) in the package.")

    def _plan(self, options: dict[str, Any]) -> evidence_pilot.EvidencePlan:
        plan = evidence_pilot.build_plan(options["package"])
        if options["mode"] == "plan":
            self._write_manifest(options, plan.manifest)
        summary = {
            "package_sha256": plan.digest,
            "manifest_sha256": plan.manifest.digest() if plan.manifest is not None else "",
            "plan_sha256": evidence_pilot.plan_digest(plan),
            "database_state": plan.database_state,
            "existing_matters": plan.existing_matters,
            "expected_writes": plan.expected_writes(),
            "held": _keys(plan, evidence_pilot.NEEDS_REVIEW),
            "rejected": _keys(plan, evidence_pilot.REJECTED),
            "organisations": sorted(evidence_pilot.approved_organisations(plan.package)),
            "warnings": {
                topic.reference: topic.warnings for topic in plan.topics if topic.warnings
            },
            "problems": plan.problems,
        }
        if options["mode"] == "plan":
            self._emit(options, summary)
            if plan.problems:
                raise CommandError(f"{len(plan.problems)} problem(s) block the apply.")
        return plan

    def _apply(self, options: dict[str, Any]) -> None:
        plan = self._plan(options)
        report = evidence_pilot.apply_plan(
            plan,
            operator_intent=options["operator_intent"],
            expect_package_sha256=options["expect_package_sha256"],
            expect_plan_sha256=options["expect_plan_sha256"],
            backup_set=options["backup_set"],
        )
        if report.already_applied:
            self.stdout.write(
                self.style.WARNING(
                    "Already applied: this package is the database's business data. "
                    "Nothing was written."
                )
            )
            return
        self._emit(
            options,
            {
                "applied": True,
                "batch": str(report.batch_id),
                "written": report.written,
                "held": report.held,
                "rejected": report.rejected,
                "organisations_created": report.organisations_created,
                "resources_skipped": report.resources_skipped,
            },
        )

    def _verify(self, options: dict[str, Any]) -> None:
        plan = evidence_pilot.build_plan(options["package"])
        report = evidence_pilot.verify_applied(plan)
        self._emit(
            options,
            {
                "verdict": "PASS" if report.ok else "FAIL",
                "checked": report.checked,
                "problems": report.problems,
            },
        )
        if not report.ok:
            raise CommandError(f"{len(report.problems)} verification problem(s).")

    def _emit(self, options: dict[str, Any], summary: dict[str, Any]) -> None:
        if options["json"]:
            self.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
            return
        for key, value in summary.items():
            if isinstance(value, list):
                self.stdout.write(f"{key}: {len(value)}")
                for item in value:
                    self.stdout.write(f"  - {item}")
            elif isinstance(value, dict):
                self.stdout.write(f"{key}:")
                for inner, item in value.items():
                    self.stdout.write(f"  {inner}: {item}")
            else:
                self.stdout.write(f"{key}: {value}")


def _keys(plan: evidence_pilot.EvidencePlan, review: str) -> list[str]:
    """The record keys (and held current steps) the plan leaves out for one reason."""
    keys = [
        record["key"]
        for topic in plan.topics
        for record in topic.held_records()
        if record.get("review") == review
    ]
    keys += [
        f"{topic.reference}-NEXT"
        for topic in plan.topics
        if (topic.topic.get("next_action") or {}).get("review") == review
    ]
    return keys
