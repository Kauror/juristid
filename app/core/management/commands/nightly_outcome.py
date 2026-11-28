"""Classify one step of the nightly cycle from its own records (ADR 0229 §4).

    manage.py nightly_outcome --step register-watch --since 2026-11-25T22:00:05Z --rc 0
    manage.py nightly_outcome --step government-agenda --since … --rc 0 --reported '{…}'

It reads run records and settings and prints one line,
``NIGHTLY-OUTCOME {"step": …, "outcome": …, "reason": …, "counts": {…}}``
(`app.core.nightly_outcome`). The host's nightly cycle calls it after each step.
With ``--record`` it also keeps that verdict in the database cache for Haldus
(ADR 0231); it writes nothing else. Exit 0 whenever it could answer.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from app.core import nightly_outcome


class Command(BaseCommand):
    help = "Ütleb, mida öise tsükli üks samm tegelikult tegi (ainult loeb)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--step", required=True, choices=nightly_outcome.STEPS)
        parser.add_argument(
            "--since", required=True, help="the cycle's start, ISO 8601 with offset"
        )
        parser.add_argument("--rc", type=int, required=True, help="the step's exit code")
        parser.add_argument("--reported", default="", help="the step's OUTCOME-JSON counts")
        parser.add_argument(
            "--manual", action="store_true", help="a person's recovery run (`--manual`)"
        )
        parser.add_argument(
            "--record",
            action="store_true",
            help="also keep the verdict for Haldus (the database cache; ADR 0231)",
        )
        parser.add_argument("--seconds", type=int, default=None, help="how long the step ran")
        parser.add_argument("--night", default="", help="the cycle's run name")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            since = dt.datetime.fromisoformat(options["since"].replace("Z", "+00:00"))
        except ValueError as error:
            raise CommandError(f"--since is not a moment: {options['since']}") from error
        if since.tzinfo is None:
            raise CommandError("--since needs an offset (UTC: Z)")
        reported = None
        if options["reported"]:
            try:
                reported = json.loads(options["reported"])
            except ValueError as error:
                raise CommandError("--reported is not JSON") from error
            if not isinstance(reported, dict):
                raise CommandError("--reported is not a JSON object")
        result = nightly_outcome.classify(
            options["step"],
            since=since,
            rc=options["rc"],
            reported=reported,
            manual=options["manual"],
        )
        if options["record"]:
            try:
                nightly_outcome.remember(
                    result,
                    rc=options["rc"],
                    seconds=options["seconds"],
                    night=options["night"],
                    cycle_start=since,
                    manual=options["manual"],
                )
            except Exception as error:  # the verdict still reaches the host's ledger
                self.stderr.write(f"could not keep the verdict for Haldus: {type(error).__name__}")
        self.stdout.write(result.line())
