"""Seed the public institution reference data.

    python manage.py seed_public_organisations

The current Estonian ministries. This is public information rather than Koda
data, so unlike `seed_dev_data` it is safe — and intended — to run in a real
deployment too: a secure environment needs the actual ministries, not
`Näidisministeerium`.

Idempotent. It adds what is missing and never renames, retypes or removes an
institution that already exists, because by the second run somebody may have
edited one deliberately.

**For a real deployment, prefer `manage.py reference_data`.** This command seeds
the ministries and writes immediately; the guarded path plans first, covers the
whole reviewed public baseline including Riigikogu, Vabariigi Valitsus and the
two EU institutions, and applies only against a digest a person read (ADR 0027).
This one remains for a quick development database.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from app.organisations.services import seed_reference_organisations


class Command(BaseCommand):
    help = "Add the current Estonian ministries as reference Organisations. Idempotent."

    def handle(self, *args: Any, **options: Any) -> None:
        result = seed_reference_organisations()

        for name in result.created:
            self.stdout.write(f"  + {name}")
        self.stdout.write(
            self.style.SUCCESS(
                f"{result.total} reference organisations: {len(result.created)} created, "
                f"{len(result.existing)} already present, {result.aliases_added} aliases added."
            )
        )
