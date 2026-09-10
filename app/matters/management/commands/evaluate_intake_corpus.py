"""Score the `Uus teema` reader against the evaluation corpus. Read-only.

A developer tool, and the counterpart to `evaluate_intake_suggestions`. That one
measures the rules against Matters the department has actually filed, which is
the honest test and needs real data to run at all. This one measures them
against invented documents whose right answers are written down, which is the
repeatable test and runs anywhere — including on a machine that has never seen
a member's correspondence.

Both are needed and neither replaces the other: a corpus can only contain the
mistakes somebody thought of, and real data can only be looked at where it
lives.

What it prints is three counts per field — **correct, missed, wrong** — and a
precision figure. It never averages them into a score, because the three are
not worth the same: a missed autofill costs somebody ten seconds of typing, and
a wrong one puts a plausible false value into a record. Optimise the last column
to zero first, and only then the middle one down (assisted-intake brief §28).

Writes nothing, opens no file, reads no evidence. The corpus is Python.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser


class Command(BaseCommand):
    help = "Score the Uus teema reader against the invented evaluation corpus. Read-only."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--case",
            default="",
            help="Score one case by name and print its candidates with their evidence.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # Imported here rather than at module level: the corpus lives with the
        # tests, and a management command that imported `tests` on load would
        # make `tests` a runtime dependency of the application image.
        from dataclasses import replace

        from app.matters.intake_suggestions.evaluation import (
            SCORED_FIELDS,
            Scorecard,
            describe,
            evaluate,
            resolve,
        )
        from app.matters.intake_suggestions.resolvers import (
            load_organisation_catalogue,
            load_policy_areas,
        )
        from app.organisations.models import Organisation
        from tests.intake_corpus import CASES

        wanted = (options["case"] or "").strip()
        organisations = load_organisation_catalogue()
        policy_areas = load_policy_areas()
        organisation_ids = {name: pk for pk, name in Organisation.objects.values_list("id", "name")}
        area_ids = {key: area.pk for key, area in policy_areas.items()}

        missing = [
            expectation.sender
            for expectation, _ in CASES
            if expectation.sender and expectation.sender not in organisation_ids
        ]
        if missing:
            # Said rather than scored around. Without the catalogue every sender
            # is a miss, and a scorecard reporting six misses because a fixture
            # was not loaded is worse than one that refuses to print.
            self.stdout.write(
                self.style.WARNING(
                    "Kataloogist puuduvad korpuse saatjad: "
                    + ", ".join(sorted(set(missing)))
                    + ". Saatja tulemused ei ole tähenduslikud."
                )
            )

        card = Scorecard()
        expectations: dict[str, Any] = {}
        for expectation, analysis_input in CASES:
            if wanted and expectation.name != wanted:
                continue
            resolved = replace(
                expectation,
                sender=(
                    resolve((expectation.sender,), organisation_ids)[0]
                    if expectation.sender in organisation_ids
                    else None
                ),
                policy_areas=resolve(expectation.policy_areas, area_ids),
            )
            expectations[expectation.name] = expectation
            decided = evaluate(
                analysis_input,
                resolved,
                organisations=organisations,
                policy_areas=policy_areas,
                scorecard=card,
            )
            if wanted:
                self._detail(expectation, decided)

        if not expectations:
            self.stdout.write(self.style.WARNING(f"Sellist juhtumit ei ole: {wanted}"))
            return

        self.stdout.write("")
        for field_name in SCORED_FIELDS:
            self.stdout.write(card.row(field_name))

        self.stdout.write("")
        if card.wrong:
            self.stdout.write(self.style.ERROR(f"VALE eeltäitmisi: {len(card.wrong)}"))
            for outcome in card.wrong:
                self.stdout.write("  " + describe(outcome, expectations[outcome.case]))
        else:
            self.stdout.write(self.style.SUCCESS("Ühtegi väära eeltäitmist ei ole."))

        if card.missed:
            self.stdout.write(f"Täitmata jäi {len(card.missed)}:")
            for outcome in card.missed:
                self.stdout.write("  " + describe(outcome, expectations[outcome.case]))

    def _detail(self, expectation: Any, analysis: Any) -> None:
        """One case, with the evidence behind every candidate.

        The reason `--case` exists: a number in the table above says a field
        was missed, and the only useful next question is what the analyser
        *did* see and why it was not enough.
        """
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(expectation.name))
        if expectation.about:
            self.stdout.write(f"  {expectation.about}")
        for name, suggestions in analysis.fields.items():
            for candidate in suggestions.candidates:
                mark = "*" if candidate.prefilled else " "
                self.stdout.write(
                    f"  {mark} {name:<22} {candidate.confidence:<6} {candidate.rule:<28} "
                    f"{candidate.display[:48]}"
                )
            if suggestions.conflict:
                self.stdout.write(f"    ! vastuolu: {suggestions.note}")
