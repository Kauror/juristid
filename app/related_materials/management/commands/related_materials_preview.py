"""Print what «Võimalikud seosed» would show for one Matter. Read-only.

A developer's instrument for judging the rules against a database the
developer is allowed to read. It authorizes as a named user, so it can show no
more than that person would see on the page; it prints titles cut short and no
body text; and it runs inside a transaction it rolls back, so even a defect in
the engine could not leave a row behind.

Refuses a `REAL_DATA_ALLOWED` environment unless `--real-data` is passed, in
the same spirit as the seeders' refusal in the other direction: real Matters
are read only where somebody has said so on the command line.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from app.accounts.models import User
from app.matters.models import Matter
from app.related_materials import engine

TITLE_WIDTH = 80


def _short(value: str) -> str:
    return value if len(value) <= TITLE_WIDTH else value[: TITLE_WIDTH - 1] + "…"


class Command(BaseCommand):
    help = "Näita ühe teema võimalikke seoseid nii, nagu nimetatud kasutaja neid näeks."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("reference", help="Teema viide, näiteks 2026_31.")
        parser.add_argument("--viewer", required=True, help="Kasutaja UPN, kellena lugeda.")
        parser.add_argument(
            "--limit", type=int, default=engine.MAX_LIMIT, help="Mitu kandidaati loendis."
        )
        parser.add_argument(
            "--real-data",
            action="store_true",
            help="Luba lugeda keskkonnas, kus REAL_DATA_ALLOWED on sees.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if settings.REAL_DATA_ALLOWED and not options["real_data"]:
            raise CommandError(
                "REAL_DATA_ALLOWED is set. This reads real Matters; pass --real-data only in a "
                "session that is authorised to look at them."
            )
        viewer = User.objects.filter(upn=options["viewer"], is_active=True).first()
        if viewer is None:
            raise CommandError("Sellist aktiivset kasutajat ei ole.")
        parsed = Matter.parse_reference(options["reference"])
        if parsed is None:
            raise CommandError("Viide peab olema kujul AAAA_N.")
        year, number = parsed
        # One message for "does not exist" and "may not see": the command must
        # not tell its caller which of the two is true (docs/adr/0005).
        matter = (
            Matter.objects.visible_to(viewer)
            .filter(reference_year=year, reference_number=number)
            .select_related("addressee_organisation", "stage", "superseded_by")
            .prefetch_related("tags", "policy_areas", "source_organisations")
            .first()
        )
        if matter is None:
            raise CommandError("Sellist teemat ei ole selle kasutaja vaates.")

        with transaction.atomic():
            result = engine.suggestions_for(
                matter, viewer, limit=options["limit"], include_hidden=True
            )
            transaction.set_rollback(True)

        self.stdout.write(f"{matter.display_reference}  {_short(matter.title)}")
        self.stdout.write(f"viewer: {viewer.upn}  ({viewer.role})")
        self.stdout.write("")
        self.stdout.write(f"Võimalikud seotud teemad ({len(result.matters)}):")
        for item in result.matters:
            self.stdout.write(
                f"  {item.score:5.2f}  {item.matter.display_reference or '—':<10} "
                f"{item.state_label:<7} {_short(item.matter.title)}"
            )
            for reason in item.reasons:
                self.stdout.write(f"           · {reason}")
        self.stdout.write("")
        self.stdout.write(f"Varasemad arvamused ja arhiivimaterjal ({len(result.materials)}):")
        for material in result.materials:
            when = material.date.isoformat() if material.date else "—"
            self.stdout.write(
                f"  {material.score:5.2f}  {material.label:<16} {when:<10} "
                f"{material.source_reference or '':<10} {_short(material.title)}"
            )
            for reason in material.reasons:
                self.stdout.write(f"           · {reason}")
        self.stdout.write("")
        self.stdout.write(f"Peidetud: {result.hidden_count}")
        self.stdout.write("Kirjutatud: 0 rida.")
