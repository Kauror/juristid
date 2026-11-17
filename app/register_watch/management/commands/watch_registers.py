"""Check the registers for new documents on the open Teemad (ADR 0216).

    manage.py watch_registers [--teema 2026_190 ...] [--no-ai] [--dry-run]

Run nightly by the host's cron. It stores what is new as proposals; nothing on a
Teema changes until a lawyer adds a proposal. ``--dry-run`` does the whole run
and rolls it back.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.matters.models import Matter
from app.register_watch import watch


class _RollBack(Exception):
    pass


class Command(BaseCommand):
    help = "Kontrollib avatud teemade registreid uute dokumentide suhtes."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--teema", action="append", default=[], help="YYYY_N; may repeat")
        parser.add_argument("--no-ai", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def _matters(self, refs: list[str]) -> list[Matter] | None:
        if not refs:
            return None
        chosen = []
        for ref in refs:
            year, _, number = ref.partition("_")
            if not (year.isdigit() and number.isdigit()):
                raise CommandError(f"not a Teema reference: {ref}")
            matter = (
                watch.watched_matters()
                .filter(reference_year=int(year), reference_number=int(number))
                .first()
            )
            if matter is None:
                raise CommandError(f"no open Teema {ref}")
            chosen.append(matter)
        return chosen

    def handle(self, *args: Any, **options: Any) -> None:
        matters = self._matters(options["teema"])
        use_ai = not options["no_ai"] and not options["dry_run"]
        if not options["dry_run"]:
            # No surrounding transaction: a run spaces its calls over minutes,
            # and each finding is its own small write.
            self._report("DONE", watch.run(matters, use_ai=use_ai))
            return
        try:
            with transaction.atomic():
                self._report("DRY-RUN", watch.run(matters, use_ai=False))
                raise _RollBack
        except _RollBack:
            return

    def _report(self, mode: str, record: Any) -> None:
        self.stdout.write(
            f"{mode} checked={record.targets_checked} new={record.findings_new} "
            f"offered={record.findings_offered} errors={len(record.errors)} "
            f"ai_calls={record.ai_calls} "
            f"ai_tokens={record.ai_input_tokens}/{record.ai_output_tokens}"
        )
        for error in record.errors[:20]:
            self.stdout.write(f"  ERROR {error}")
