"""Apply the Koda house style to AI lines saved before it existed (owner, 2026-11-23).

    manage.py fix_koda_style [--dry-run]

Runs `house_style.fix` over a finding's line, a new draft's reason and an agenda
item's reason, and saves only the rows it changed, printing each change.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from app.register_watch.house_style import fix
from app.register_watch.models import AgendaItem, TopicCandidate, WatchFinding

FIELDS = ((WatchFinding, "ai_note"), (TopicCandidate, "reason"), (AgendaItem, "reason"))


class Command(BaseCommand):
    help = "Parandab AI kirjutatud ridades koja nimekuju."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        changed = 0
        with transaction.atomic():
            for model, name in FIELDS:
                for row in model.objects.exclude(**{name: ""}).only("pk", name).iterator():
                    before = getattr(row, name)
                    after = fix(before)
                    if after == before:
                        continue
                    changed += 1
                    self.stdout.write(f"{model.__name__} {row.pk}\n  - {before}\n  + {after}")
                    if not options["dry_run"]:
                        setattr(row, name, after)
                        row.save(update_fields=[name, "updated_at"])
        self.stdout.write(f"{'DRY-RUN' if options['dry_run'] else 'DONE'} changed={changed}")
