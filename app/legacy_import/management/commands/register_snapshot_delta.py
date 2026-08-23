"""What changed in the register since production was built.

    python manage.py register_snapshot_delta --workbook PATH
    python manage.py register_snapshot_delta --workbook PATH --expect-sha256 SHA
    python manage.py register_snapshot_delta --workbook PATH --json

**This command never writes.** It opens a workbook read-only, compares it
against the immutable source provenance production already holds, and prints
what differs. There is no ``--apply``, and adding one would turn a report into
the Excel-to-Juristid bridge the cutover exists to remove.

It exists so the final cutover is a decision rather than an act of faith. Excel
stays in operational use while this system is polished and judged; that parallel
run is safe exactly as long as somebody can answer, on demand and deterministic
ally, "what has changed in the spreadsheet since production was built, and has
anybody done native work that would be overwritten by importing it".

``--expect-sha256`` is how an operator states which bytes they meant. Given a
digest that does not match, the command refuses rather than reporting on a file
nobody approved — the same reasoning that keeps the reviewed snapshot list in
source rather than on a command line.
"""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from app.legacy_import.snapshot_delta import DeltaRefused, DeltaReport, build_report


class Command(BaseCommand):
    help = "Report how a register workbook differs from what production holds. Reads only."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--workbook",
            required=True,
            help="Path to the .xlsx snapshot to compare. Opened read-only and never rewritten.",
        )
        parser.add_argument(
            "--expect-sha256",
            default="",
            help="Refuse unless the workbook hashes to exactly this digest.",
        )
        parser.add_argument(
            "--years",
            default="2025,2026",
            help=(
                "Sheet years the current portfolio is recomputed over. "
                "Defaults to the reviewed cutover's own scope."
            ),
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit the whole report as JSON instead of the readable summary.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        raw_years = str(options["years"]).strip()
        try:
            years = tuple(int(part) for part in raw_years.split(",") if part.strip())
        except ValueError as error:
            raise CommandError(
                f"--years must be a comma-separated list of years: {error}"
            ) from error
        if not years:
            raise CommandError("--years must name at least one sheet year.")

        try:
            report = build_report(
                workbook_path=options["workbook"],
                expected_sha256=options["expect_sha256"],
                scope_years=years,
                generated_at=timezone.now(),
            )
        except FileNotFoundError as error:
            raise CommandError(str(error)) from error
        except DeltaRefused as error:
            # A refusal, never an empty report. "Nothing changed" and "we did not
            # run the comparison" look the same in a summary and mean opposite
            # things about the register.
            raise CommandError(str(error)) from error

        if options["json"]:
            self.stdout.write(
                json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            )
            return

        self._render(report)

    # -- readable output ---------------------------------------------------

    def _render(self, report: DeltaReport) -> None:
        write = self.stdout.write
        write("")
        write(self.style.MIGRATE_HEADING("Registri hetktõmmise võrdlus"))
        write(f"  töövihik            {report.workbook_name}")
        write(f"  SHA-256             {report.workbook_sha256}")
        if report.expected_sha256:
            write(f"  oodatud SHA-256     {report.expected_sha256}  (kattub)")
        write(f"  baas                {', '.join(report.baseline_snapshots) or '—'}")
        write(f"  jooksva töö aastad  {', '.join(str(year) for year in report.scope_years)}")

        write("")
        write(self.style.MIGRATE_HEADING("Read"))
        write(f"  IDENTSED            {report.identical}")
        write(f"  MUUDETUD            {report.changed}")
        write(f"  UUED                {report.new}")
        write(f"  KADUNUD             {report.removed}")
        write(f"  sisulisi välju      {report.semantic_field_count}")

        write("")
        write(self.style.MIGRATE_HEADING("Lehed"))
        for sheet in report.sheets:
            write(
                f"  {sheet['sheet']:<6} baas {sheet['baseline_rows']:>5}"
                f"  töövihik {sheet['workbook_rows']:>5}  vahe {sheet['delta']:>+4}"
            )

        changed = report.changed_rows
        if changed:
            write("")
            write(self.style.MIGRATE_HEADING("Väljade muutused"))
            for row in changed:
                write(f"  {row.reference}  {row.title[:70]}")
                for delta in row.fields:
                    marker = "SISULINE" if delta.semantic_differs else "vormiline"
                    write(f"    [{marker}] {delta.header} ({delta.canonical_field})")
                    write(f"      baas:     {delta.baseline!r}")
                    write(f"      töövihik: {delta.workbook!r}")

        for row in report.rows:
            if row.status == "NEW":
                write(f"  UUS      {row.reference}  {row.title[:70]}")
            elif row.status == "REMOVED":
                write(f"  KADUNUD  {row.reference}  {row.title[:70]}")

        write("")
        write(self.style.MIGRATE_HEADING("Jooksev töö"))
        impact = report.portfolio
        write(f"  töövihiku järgi     {impact.current}   (tootmises {impact.production_current})")
        for sheet in sorted(set(impact.by_sheet) | set(impact.production_by_sheet)):
            write(
                f"    {sheet:<6} töövihik {impact.by_sheet.get(sheet, 0):>4}"
                f"   tootmine {impact.production_by_sheet.get(sheet, 0):>4}"
            )
        write(f"  koostamisel         {impact.drafting}   (tootmises {impact.production_drafting})")
        write(f"  lõppenuks           {impact.retire}")
        write(f"  jätkub mujal        {impact.supersede}")
        write(f"  ülevaatust vajab    {impact.review}")
        if impact.identities_match:
            write(self.style.SUCCESS("  identiteedid kattuvad tootmisega"))
        else:
            # Named before the counts are believed. Equal totals with a swapped
            # membership is the failure a headline figure cannot show.
            write(self.style.WARNING("  identiteedid EI kattu tootmisega"))
            for reference in impact.would_activate:
                write(f"    aktiveeruks   {reference}")
            for reference in impact.would_retire:
                write(f"    lõpetataks    {reference}")

        write("")
        write(self.style.MIGRATE_HEADING("Jätkumised"))
        if report.continuations:
            for item in report.continuations:
                write(
                    f"  {item.reference}: {item.baseline_verdict}"
                    f"{'/' + item.baseline_target if item.baseline_target else ''}"
                    f" → {item.workbook_verdict}"
                    f"{'/' + item.workbook_target if item.workbook_target else ''}"
                )
        else:
            write("  muutusi ei ole")

        write("")
        write(self.style.MIGRATE_HEADING("OneNote lingid"))
        if report.hyperlinks:
            for link in report.hyperlinks:
                write(f"  {link.reference} ({link.column})")
                write(f"    baas:     {link.baseline or '—'}")
                write(f"    töövihik: {link.workbook or '—'}")
        else:
            write("  muutusi ei ole")

        write("")
        write(self.style.MIGRATE_HEADING("Omakirjed pärast kataloogimist"))
        write(f"  sündmusi            {len(report.native_writes)}")
        for item in report.native_writes[:20]:
            write(f"    {item.occurred_at}  {item.event_type:<28} {item.reference}  {item.actor}")

        write("")
        write(self.style.MIGRATE_HEADING("Topeltkirjutuse konfliktid"))
        if report.conflicts:
            write(self.style.ERROR(f"  {len(report.conflicts)} konflikti — lahendab inimene"))
            for conflict in report.conflicts:
                write(f"    {conflict.reference}: {', '.join(conflict.changed_fields)}")
                for event in conflict.native_events:
                    write(f"      {event.occurred_at}  {event.event_type}  {event.actor}")
        else:
            write("  ei leitud")

        if report.findings:
            write("")
            write(self.style.WARNING("Leiud"))
            for finding in report.findings:
                write(f"  {finding}")

        write("")
        write(self.style.SUCCESS("Andmebaasi ei kirjutatud."))
