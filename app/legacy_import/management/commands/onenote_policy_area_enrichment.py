"""Plan — and, behind a digest, apply — PolicyAreas from the OneNote filing structure.

    python manage.py onenote_policy_area_enrichment inventory
    python manage.py onenote_policy_area_enrichment plan
    python manage.py onenote_policy_area_enrichment apply --expect-plan-sha256 <digest>

``inventory`` is where this starts, and it is not optional. It lists the filing
locations the corpus actually contains, how many Matters sit under each, and
whether an active PolicyArea already carries that exact name. Mappings are
written from that, by a person, into ``REVIEWED_ALIAS_RULES`` — which is a code
change and therefore reviewed. Nothing here invents what ``Muud`` or ``ARHIIV``
meant.

``plan`` decides everything and writes nothing. ``apply`` adds only the missing
relations and only for a digest somebody read; it never removes an area, never
creates one, and never creates a Tag.

Section names appear in the output. They are the department's own filing
vocabulary and an operator cannot review a mapping without seeing which
locations went unmapped; Matter titles do not appear.
"""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.legacy_import.onenote_policy_areas import (
    MAPPING_CLASSES,
    PolicyAreaPlanChanged,
    UnknownCapture,
    apply_policy_area_plan,
    build_policy_area_plan,
    inventory,
    summary,
)

_CLASS_LABELS: dict[str, str] = {
    "EXACT_NAME": "section name is exactly an active area's name",
    "REVIEWED_ALIAS": "reviewed mapping",
    "AMBIGUOUS": "name shared by more than one active area",
    "MISSING_TARGET": "reviewed rule names no active area (fix the rule)",
    "UNMAPPED": "no mapping; nothing is guessed",
}


class Command(BaseCommand):
    help = (
        "Propose canonical PolicyAreas from reviewed OneNote section mappings, "
        "additively and without touching the captured section itself."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("mode", choices=["inventory", "plan", "apply"])
        parser.add_argument(
            "--expect-plan-sha256",
            default="",
            help="Required by apply: the plan digest a person reviewed.",
        )
        parser.add_argument("--json", action="store_true", help="Print JSON instead of text.")

    def handle(self, *args: Any, **options: Any) -> None:
        if options["mode"] == "inventory":
            self._inventory(json_output=bool(options["json"]))
            return

        try:
            plan = build_policy_area_plan()
        except UnknownCapture as error:
            raise CommandError(str(error)) from error

        figures = summary(plan)
        if options["json"]:
            self.stdout.write(json.dumps(figures, indent=2, ensure_ascii=False))
        else:
            self._report(figures)

        if options["mode"] == "plan":
            self.stdout.write("")
            self.stdout.write("Plan only: nothing was written to the database.")
            self.stdout.write(f"  plan digest  {figures['plan_sha256']}")
            return

        expected = str(options["expect_plan_sha256"]).strip()
        if not expected:
            raise CommandError("apply needs --expect-plan-sha256 from a reviewed plan run.")

        try:
            result = apply_policy_area_plan(plan, expect_plan_sha256=expected)
        except PolicyAreaPlanChanged as error:
            raise CommandError(str(error)) from error

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Applied"))
        self.stdout.write(f"  Matters changed   {result.matters_changed}")
        self.stdout.write(f"  relations added   {result.relations_added}")
        self.stdout.write(f"  mapping version   {result.mapping_version}")
        self.stdout.write(
            "  no area was removed, no taxonomy row was created, no Tag was "
            "created, and no captured section was rewritten."
        )

    # -- output ------------------------------------------------------------

    def _inventory(self, *, json_output: bool) -> None:
        rows = inventory()
        if json_output:
            self.stdout.write(json.dumps(rows, indent=2, ensure_ascii=False))
            return
        self.stdout.write(self.style.MIGRATE_HEADING("OneNote filing locations"))
        self.stdout.write(f"  distinct locations  {len(rows)}")
        self.stdout.write("")
        self.stdout.write(f"  {'links':>6} {'matters':>8}  {'name=area':>9}  location")
        for row in rows:
            location = f"{row['section_group']} → {row['section']}".strip(" →")
            flag = "yes" if row["matches_active_area_name"] else "—"
            self.stdout.write(f"  {row['links']:>6} {row['matters']:>8}  {flag:>9}  {location}")
        self.stdout.write("")
        self.stdout.write(
            "Nothing above is a mapping. Add reviewed entries to "
            "REVIEWED_ALIAS_RULES in app/legacy_import/onenote_policy_areas.py."
        )

    def _report(self, figures: dict[str, Any]) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("OneNote → PolicyArea enrichment"))
        self.stdout.write(f"  mapping version     {figures['mapping_version']}")
        self.stdout.write(f"  reviewed rules      {figures['reviewed_rules']}")
        self.stdout.write(f"  capture             {figures['capture_sha256'][:16]}…")
        self.stdout.write(f"  Matters considered  {figures['matters_considered']}")
        self.stdout.write(f"  page links used     {figures['links_considered']}")
        self.stdout.write(f"  background excluded {figures['background_links_excluded']}")
        self.stdout.write(f"  TEST links excluded {figures['test_matter_links_excluded']}")
        self.stdout.write("")

        self.stdout.write(f"Filing locations ({figures['distinct_locations']})")
        for name in MAPPING_CLASSES:
            self.stdout.write(f"  {_CLASS_LABELS[name]:<48} {figures['location_classes'][name]}")

        self.stdout.write("")
        self.stdout.write("Proposals")
        self.stdout.write(f"  {'Matter ↔ area pairs proposed':<48} {figures['proposals']}")
        self.stdout.write(f"  {'already present (no change)':<48} {figures['already_present']}")
        self.stdout.write(f"  {'new relations':<48} {figures['new_relations']}")
        self.stdout.write(f"  {'Matters gaining an area':<48} {figures['matters_with_additions']}")

        for label, key in (
            ("Unmapped locations", "unmapped_locations"),
            ("Ambiguous locations", "ambiguous_locations"),
            ("Misconfigured rules", "misconfigured_rules"),
        ):
            if figures[key]:
                self.stdout.write("")
                self.stdout.write(label)
                for value in figures[key]:
                    self.stdout.write(f"  {value}")
