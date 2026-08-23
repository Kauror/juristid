"""Plan, apply, verify and measure the reviewed reference-data baseline.

    python manage.py reference_data plan
    python manage.py reference_data apply --expect-plan-sha256 <digest>
    python manage.py reference_data verify
    python manage.py reference_data coverage --expect-register-snapshot-sha256 <sha>

``plan`` is read-only and is where an operator starts: what the reviewed
baseline expects, what the database holds, what is missing, and what conflicts.
It ends with a digest over exactly those proposed changes.

``apply`` performs them and nothing else, in one transaction, only for a digest
somebody read. It creates missing public institutions and adds missing reviewed
aliases. It never renames, retypes, re-codes, merges, deactivates or moves an
alias between institutions — see ``app.core.reference_data`` for why each of
those is off the table.

``verify`` answers the yes/no question and exits non-zero when the baseline is
broken. This is what deployment readiness leans on.

``coverage`` is a read-only diagnostic over the imported register: how much of
the ``KELLELT``/``KELLELE`` column the reviewed institutions would actually
resolve, split by era and direction. It writes nothing at all — no Matter
sender, no addressee — because whether historic strings should become canonical
relationships is a separate decision this measurement exists to inform.

Policy areas are read by all four modes and written by none: they arrive through
``taxonomy/0002_reference_policy_areas``, which is where a vocabulary change is
reviewed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.core.reference_data import (
    ReferenceDataConflict,
    ReferencePlanChanged,
    apply_reference_plan,
    build_reference_plan,
    verify_reference_data,
)


class Command(BaseCommand):
    help = (
        "Plan, apply and verify the reviewed public reference data, and measure what it "
        "would resolve in the imported register. Only `apply` writes, and only additively."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("mode", choices=["plan", "apply", "verify", "coverage"])
        parser.add_argument(
            "--expect-plan-sha256",
            default="",
            help="Required by apply: the plan digest a person reviewed.",
        )
        parser.add_argument(
            "--expect-register-snapshot-sha256",
            default="",
            help=(
                "Required by coverage: the register snapshot to measure. Named rather than "
                "guessed, because a database imported twice holds two registers."
            ),
        )
        parser.add_argument(
            "--output",
            default="",
            help=(
                "coverage only: write the unresolved raw values to this path for human "
                "review. They are register content and are never printed to the terminal. "
                "Choose a path outside the checkout."
            ),
        )
        parser.add_argument("--json", action="store_true", help="Print JSON instead of text.")

    def handle(self, *args: Any, **options: Any) -> None:
        mode = options["mode"]
        if mode == "coverage":
            self._coverage(options)
            return
        if mode == "verify":
            self._verify(json_output=bool(options["json"]))
            return

        plan = build_reference_plan()
        if options["json"]:
            self.stdout.write(json.dumps(_plan_json(plan), indent=2, ensure_ascii=False))
        else:
            self._report_plan(plan)

        if mode == "plan":
            self.stdout.write("")
            self.stdout.write("Plan only: nothing was written to the database.")
            self.stdout.write(f"  plan digest  {plan.digest()}")
            return

        expected = str(options["expect_plan_sha256"]).strip()
        if not expected:
            raise CommandError("apply needs --expect-plan-sha256 from a reviewed plan run.")

        try:
            result = apply_reference_plan(expected_sha256=expected)
        except ReferencePlanChanged as error:
            raise CommandError(str(error)) from error
        except ReferenceDataConflict as error:
            raise CommandError(str(error)) from error

        self.stdout.write("")
        for name in result.organisations_created:
            self.stdout.write(f"  + {name}")
        for alias in result.aliases_added:
            self.stdout.write(f"  + {alias}")
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(result.organisations_created)} organisation(s) created, "
                f"{len(result.aliases_added)} alias(es) added. "
                "No existing row was renamed, retyped or removed."
            )
        )
        if plan.areas_missing:
            self.stdout.write(
                self.style.WARNING(
                    f"WARNING: {len(plan.areas_missing)} reviewed policy area(s) are still "
                    "missing. They arrive with the schema — run `manage.py migrate`."
                )
            )

    # -- plan ---------------------------------------------------------------

    def _report_plan(self, plan: Any) -> None:
        self.stdout.write("Policy areas")
        self.stdout.write(f"  baseline     {plan.policy_area_version}")
        self.stdout.write(f"  expected     {len(plan.policy_areas)}")
        self.stdout.write(f"  present      {len(plan.areas_present)}")
        self.stdout.write(f"  missing      {len(plan.areas_missing)}")
        self.stdout.write(f"  conflicting  {len(plan.areas_conflicting)}")
        for finding in plan.areas_missing:
            self.stdout.write(f"    missing  {finding.name} ({finding.key})")
        for finding in plan.areas_conflicting:
            self.stdout.write(self.style.ERROR(f"    conflict {finding.key}: {finding.detail}"))
        if plan.areas_missing:
            self.stdout.write(
                "  Policy areas are not created here. They arrive with "
                "taxonomy/0002_reference_policy_areas — run `manage.py migrate`."
            )

        self.stdout.write("")
        self.stdout.write("Organisations")
        self.stdout.write(f"  baseline     {plan.organisation_version}")
        self.stdout.write(f"  expected     {len(plan.organisations)}")
        self.stdout.write(f"  present      {len(plan.organisations_present)}")
        self.stdout.write(f"  to create    {len(plan.organisations_to_create)}")
        self.stdout.write(f"  aliases      {plan.aliases_to_add} to add")
        self.stdout.write(f"  conflicting  {len(plan.organisations_conflicting)}")
        for finding in plan.organisations_to_create:
            self.stdout.write(f"    create   {finding.name} [{finding.organisation_type}]")
        for finding in plan.organisations:
            for alias in finding.aliases_to_add:
                self.stdout.write(f"    alias    {finding.name} ← {alias}")
            if finding.detail:
                self.stdout.write(f"    note     {finding.name}: {finding.detail}")
        for finding in plan.organisations_conflicting:
            self.stdout.write(self.style.ERROR(f"    conflict {finding.name}: {finding.detail}"))
        for finding in plan.organisations:
            for alias in finding.aliases_claimed_elsewhere:
                self.stdout.write(
                    self.style.ERROR(
                        f"    conflict {finding.name}: alias {alias!r} belongs to another "
                        "organisation and is not moved"
                    )
                )

        self.stdout.write("")
        self.stdout.write("Tags")
        self.stdout.write("  not managed by this baseline; the vocabulary is not reviewed yet")

    # -- verify -------------------------------------------------------------

    def _verify(self, *, json_output: bool) -> None:
        report = verify_reference_data()
        if json_output:
            self.stdout.write(
                json.dumps(
                    {
                        "ok": report.ok,
                        "policy_areas_present": report.policy_areas_present,
                        "policy_areas_expected": report.policy_areas_expected,
                        "organisations_present": report.organisations_present,
                        "organisations_expected": report.organisations_expected,
                        "problems": list(report.problems),
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            self.stdout.write(
                f"Policy areas   {report.policy_areas_present}/{report.policy_areas_expected}"
            )
            self.stdout.write(
                f"Organisations  {report.organisations_present}/{report.organisations_expected}"
            )
            self.stdout.write("Tags           not managed by this baseline")

        if not report.ok:
            raise CommandError(
                "The reviewed reference-data baseline is not intact:\n"
                + "\n".join(f"  - {problem}" for problem in report.problems)
            )
        self.stdout.write(self.style.SUCCESS("Reference data baseline is complete."))

    # -- coverage -----------------------------------------------------------

    def _coverage(self, options: dict[str, Any]) -> None:
        from app.legacy_import.counterparty_coverage import (
            ADDRESSEE,
            CLASSIFICATIONS,
            SOURCE,
            build_coverage_report,
        )
        from app.legacy_import.opinion_plan import OpinionPlanError, select_register_snapshot

        expected = str(options["expect_register_snapshot_sha256"]).strip()
        if not expected:
            raise CommandError(
                "coverage needs --expect-register-snapshot-sha256. Naming the register is "
                "what keeps a database that was imported twice from counting every Matter "
                "twice; `manage.py opinion_archive plan` reports the current snapshot."
            )
        try:
            # Reuses the reconciliation's own selection, so "which register is
            # current" has one answer in the product rather than two.
            current = select_register_snapshot(expected)
        except OpinionPlanError as error:
            raise CommandError(str(error)) from error

        report = build_coverage_report(snapshot_sha256=current)
        figures = report.summary()

        if options["json"]:
            self.stdout.write(json.dumps(figures, indent=2, ensure_ascii=False))
        else:
            self.stdout.write(f"Register snapshot  {current[:16]}…")
            self.stdout.write(f"Source references  {report.references_read}")
            self.stdout.write(
                f"No counterparty column  {report.rows_without_a_counterparty_column}"
            )
            self.stdout.write("")
            for era in report.eras:
                for direction in (SOURCE, ADDRESSEE):
                    counts = {
                        cls: report.counts[(era, direction, cls)]
                        for cls in CLASSIFICATIONS
                        if report.counts[(era, direction, cls)]
                    }
                    if not counts:
                        continue
                    label = "KELLELT (saatja)" if direction == SOURCE else "KELLELE (saaja)"
                    self.stdout.write(f"{era}  {label}")
                    for cls in CLASSIFICATIONS:
                        if counts.get(cls):
                            self.stdout.write(f"    {cls:<18} {counts[cls]}")
            self.stdout.write("")
            self.stdout.write("Totals")
            for cls in CLASSIFICATIONS:
                self.stdout.write(f"  {cls:<18} {report.total(cls)}")
            self.stdout.write("")
            self.stdout.write(f"Distinct unresolved values  {report.distinct_unresolved}")
            self.stdout.write(f"Matters affected            {report.matters_with_unresolved}")
            self.stdout.write(
                "The values themselves are register content and are not printed. "
                "Use --output <path> to write them somewhere an operator chose."
            )

        destination = str(options["output"]).strip()
        if destination:
            self._write_operator_artifact(Path(destination), report)

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "Diagnostic only: no Matter sender, addressee or ChangeEvent was written."
            )
        )

    def _write_operator_artifact(self, path: Path, report: Any) -> None:
        """Write the unresolved values for human review, owner-readable only.

        Refuses to write inside the checkout. This file is the one place raw
        register content leaves the database, and a path under the repository is
        one ``git add`` away from a pull request.
        """
        resolved = path.expanduser().resolve()
        checkout = Path(__file__).resolve().parents[4]
        if resolved == checkout or checkout in resolved.parents:
            raise CommandError(
                f"{resolved} is inside the checkout. Unresolved register values are source "
                "content and must not sit where a commit could pick them up."
            )
        resolved.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "snapshot_sha256": report.snapshot_sha256,
            "unresolved": report.unresolved_rows(),
        }
        resolved.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            resolved.chmod(0o600)
        except OSError:  # pragma: no cover - Windows and some mounts refuse
            pass
        self.stdout.write(
            self.style.WARNING(
                f"Wrote {len(payload['unresolved'])} unresolved value(s) to {resolved}. "
                "Human-review material: do not commit it, attach it to a PR or upload it."
            )
        )


def _plan_json(plan: Any) -> dict[str, Any]:
    return {
        "policy_area_version": plan.policy_area_version,
        "organisation_version": plan.organisation_version,
        "policy_areas": {
            "expected": len(plan.policy_areas),
            "present": len(plan.areas_present),
            "missing": [f.key for f in plan.areas_missing],
            "conflicting": [{"key": f.key, "detail": f.detail} for f in plan.areas_conflicting],
        },
        "organisations": {
            "expected": len(plan.organisations),
            "present": len(plan.organisations_present),
            "to_create": [f.name for f in plan.organisations_to_create],
            "aliases_to_add": [
                {"organisation": f.name, "alias": alias}
                for f in plan.organisations
                for alias in f.aliases_to_add
            ],
            "conflicting": [
                {"name": f.name, "detail": f.detail} for f in plan.organisations_conflicting
            ],
        },
        "tags": "not managed by this baseline",
        "plan_sha256": plan.digest(),
    }
