"""The 2026 Excel operational pilot (docs/adr/0148).

    python manage.py excel_pilot_2026 manifest --workbook W --selection S --out M
    python manage.py excel_pilot_2026 plan     --workbook W --manifest M
    python manage.py excel_pilot_2026 apply    --workbook W --manifest M \\
        --expect-manifest-sha256 D --operator-intent excel-pilot-2026 --backup-set SET

`manifest` reads worksheet 2026 and a reviewed selection and writes the manifest
— no database writes. `plan` re-derives the manifest from the workbook, resolves
every name against the database and prints what would be written — no writes.
`apply` writes it, in one transaction, only with ``JURISTID_EXCEL_PILOT`` and
``REAL_DATA_ALLOWED`` set, the typed intent, the manifest's own digest, a named
backup set, and a database holding no business Matter the pilot did not create.
Run again with the same manifest, it writes nothing.

The workbook, the selection and the manifest hold the Chamber's register content:
keep them outside the repository.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.legacy_import import excel_pilot


class Command(BaseCommand):
    help = "Build, plan or apply the 2026 Excel operational pilot (docs/adr/0148)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("mode", choices=["manifest", "plan", "apply"])
        parser.add_argument("--workbook", required=True, help="The exact .xlsx snapshot.")
        parser.add_argument("--selection", help="manifest: the reviewed selection (TOML).")
        parser.add_argument("--out", help="manifest: where to write the manifest (JSON).")
        parser.add_argument("--manifest", help="plan/apply: the reviewed manifest (JSON).")
        parser.add_argument("--expect-manifest-sha256", default="")
        parser.add_argument("--operator-intent", default="")
        parser.add_argument(
            "--backup-set",
            default="",
            help="apply: the verified backup set taken before the reset, e.g. sets/20261009T…Z.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            if options["mode"] == "manifest":
                self._manifest(options)
            else:
                self._plan_or_apply(options)
        except excel_pilot.PilotError as error:
            raise CommandError(str(error)) from error

    def _manifest(self, options: dict[str, Any]) -> None:
        if not options["selection"] or not options["out"]:
            raise CommandError("manifest needs --selection and --out.")
        reading = excel_pilot.read_pilot_sheet(options["workbook"])
        selection = excel_pilot.load_selection(options["selection"])
        manifest = excel_pilot.build_manifest(reading, selection)
        excel_pilot.write_manifest(manifest, options["out"])
        counts = manifest["counts"]
        self.stdout.write(f"Workbook   {reading.file_name}")
        self.stdout.write(f"           {reading.sha256}")
        self.stdout.write(
            f"Sample     A {counts['A']} · B {counts['B']} · C {counts['C']} "
            f"= {len(manifest['rows'])} of {len(reading.rows)} titled 2026 rows"
        )
        self.stdout.write(f"Reserve    {excel_pilot.SHEET_YEAR}_{reading.highest_number}")
        self.stdout.write(self.style.SUCCESS(f"Manifest   {excel_pilot.manifest_digest(manifest)}"))
        self.stdout.write(f"Written to {options['out']} — keep it outside the repository.")

    def _plan_or_apply(self, options: dict[str, Any]) -> None:
        if not options["manifest"]:
            raise CommandError(f"{options['mode']} needs --manifest.")
        manifest = excel_pilot.read_manifest(options["manifest"])
        plan = excel_pilot.build_plan(options["workbook"], manifest)
        for line in excel_pilot.summarise_plan(plan):
            self.stdout.write(line)

        if options["mode"] == "plan":
            self.stdout.write("")
            self.stdout.write("Plan only: nothing was written.")
            if plan.problems:
                raise CommandError(f"{len(plan.problems)} problem(s) block the apply.")
            return

        report = excel_pilot.apply_plan(
            plan,
            operator_intent=options["operator_intent"],
            expect_manifest_sha256=options["expect_manifest_sha256"],
            backup_set=options["backup_set"],
        )
        self.stdout.write("")
        if report.already_applied:
            self.stdout.write(
                self.style.WARNING(
                    f"Already applied: this manifest's {report.matters} Matters are the database's "
                    "business data. Nothing was written."
                )
            )
            return
        self.stdout.write(self.style.SUCCESS("Applied"))
        self.stdout.write(f"  batch                 {report.batch_id}")
        self.stdout.write(f"  matters               {report.matters}")
        for status, count in sorted(report.by_status.items()):
            self.stdout.write(f"    {status:<20} {count}")
        self.stdout.write(f"  sent opinions         {report.submissions}")
        self.stdout.write(f"  placeholder PDFs      {report.placeholders}")
        self.stdout.write(f"  follow-up checks      {len(report.follow_ups)}")
        for reference, due in report.follow_ups:
            self.stdout.write(f"    {reference:<12} due {due.isoformat() if due else '—'}")
        self.stdout.write(f"  dated actions         {report.actions}")
        self.stdout.write(f"  undated actions       {report.undated_actions}")
        self.stdout.write(f"  notes (Märkus)        {report.entries}")
        self.stdout.write(f"  deadlines answered    {report.deadlines_answered}")
        self.stdout.write(f"  deadlines declined    {report.deadlines_not_answering}")
        self.stdout.write(f"  continuations linked  {report.continuations}")
        self.stdout.write(
            f"  reference sequence    {excel_pilot.SHEET_YEAR} → {report.reserved_through}"
        )
