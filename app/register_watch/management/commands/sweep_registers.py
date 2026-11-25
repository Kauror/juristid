"""Look through the registers for new drafts that are not a Teema yet (ADR 0218).

    manage.py sweep_registers [--no-ai] [--dry-run]

Run every night by the nightly cycle (ADR 0229), after `watch_registers` (which
runs after the Lovable synchronisation). It stores what is new
as candidates on the «Uued eelnõud» page; nothing becomes a Teema until a person
adds one. ``--dry-run`` does the whole run and rolls it back.

Exit 0 when the run finished, even if some registers could not be read (they
are in the run's log). Exit 1 when the sweep is off, another run is going, or
the run itself stopped.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.core.errors import DomainError
from app.register_watch import candidates


class _RollBack(Exception):
    pass


class Command(BaseCommand):
    help = "Otsib registritest uusi eelnõusid, mis pole veel teemad."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--no-ai", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        if not candidates.enabled():
            raise CommandError("the sweep is off (JURISTID_REGISTER_SWEEP)")
        try:
            if not options["dry_run"]:
                self._report("DONE", candidates.sweep(use_ai=not options["no_ai"]))
                return
            try:
                with transaction.atomic():
                    self._report("DRY-RUN", candidates.sweep(use_ai=False))
                    raise _RollBack
            except _RollBack:
                return
        except DomainError as error:
            raise CommandError(str(error)) from None

    def _report(self, mode: str, record: Any) -> None:
        self.stdout.write(
            f"{mode} sources={record.sources_read} items={record.items_new} "
            f"candidates={record.candidates_new} errors={len(record.errors)} "
            f"ai_model={record.ai_model or '-'} ai_calls={record.ai_calls} "
            f"ai_judged={record.ai_judged} ai_failures={record.ai_failures} "
            f"ai_tokens={record.ai_input_tokens}/{record.ai_output_tokens}"
        )
        for error in record.errors[:20]:
            self.stdout.write(f"  ERROR {error}")
