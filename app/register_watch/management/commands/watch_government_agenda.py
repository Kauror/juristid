"""Read the Government's commented session agenda for «Valitsuse info» (ADR 0219).

    manage.py watch_government_agenda [--weeks N] [--no-ai] [--dry-run]

Run by the host's cron every half hour from Wednesday 12:00 to Thursday 12:00 Tallinn
time. A week whose agenda is already stored reads nothing, so the later runs that week
cost one query. ``--weeks 4`` also reads the three weeks before (once, at release).
``--dry-run`` does the whole run and rolls it back.

Exit 0 when the run finished, even if valitsus.ee could not be read (the errors are
printed); exit 1 when reading is off.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.register_watch import agenda


class _RollBack(Exception):
    pass


class Command(BaseCommand):
    help = "Loeb valitsuse istungi kommenteeritud päevakorra ja hindab selle punkte."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--weeks", type=int, default=1)
        parser.add_argument("--no-ai", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        if not agenda.enabled():
            raise CommandError("reading is off (JURISTID_REGISTER_WATCH_FETCH)")
        weeks = max(1, min(int(options["weeks"]), 12))
        if not options["dry_run"]:
            self._report("DONE", agenda.run(weeks=weeks, use_ai=not options["no_ai"]))
            return
        try:
            with transaction.atomic():
                self._report("DRY-RUN", agenda.run(weeks=weeks, use_ai=False))
                raise _RollBack
        except _RollBack:
            return

    def _report(self, mode: str, outcome: Any) -> None:
        stored = (
            ", ".join(f"{a.session_date} ({a.items.count()} punkti)" for a in outcome.stored) or "-"
        )
        u = outcome.usage
        self.stdout.write(
            f"{mode} stored={stored} judged={outcome.judged} errors={len(outcome.errors)} "
            f"ai_model={u.model or '-'} ai_calls={u.calls} ai_failures={u.failures} "
            f"ai_tokens={u.input_tokens}/{u.output_tokens}"
        )
        for error in outcome.errors[:10]:
            self.stdout.write(f"  ERROR {error}")
        if u.failures:
            self.stdout.write(f"  AI {u.last_failure}")
