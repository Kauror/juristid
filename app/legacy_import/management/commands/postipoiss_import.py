"""Load Postipoiss letters and apply the calibrated pairings (docs/adr/0216).

Phases, in this order:

* ``plan --package DIR`` — read and check everything, write nothing, print the
  manifest digest a reviewer approves.
* ``load --package DIR --expect-package-sha256 SHA`` — store the letters, files
  and proposals. Idempotent; decides nothing.
* ``auto-apply [--dry-run]`` — apply every pending pairing in the calibrated
  tier. ``--dry-run`` runs the real code and rolls back.
* ``status`` — counts.

The letters are real Koda correspondence: the write phases need
``REAL_DATA_ALLOWED``, like the opinion archive.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db.models import Count

from app.core.errors import DomainError
from app.legacy_import import postipoiss
from app.legacy_import.postipoiss_models import PostipoissLetter, PostipoissPairing


class Command(BaseCommand):
    help = "Postipoisi kirjad: plan, load, auto-apply, status."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("phase", choices=["plan", "load", "auto-apply", "status"])
        parser.add_argument("--package", default="")
        parser.add_argument("--expect-package-sha256", default="")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        phase = options["phase"]
        try:
            if phase == "status":
                self._status()
            elif phase == "plan":
                package = postipoiss.read_package(self._package(options))
                self.stdout.write(postipoiss.load(package, write=False).as_text())
            elif phase == "load":
                self._require_gate()
                if not options["expect_package_sha256"]:
                    raise CommandError("load vajab --expect-package-sha256 (plan prindib selle).")
                package = postipoiss.read_package(
                    self._package(options), expected_sha256=options["expect_package_sha256"]
                )
                self.stdout.write(postipoiss.load(package, write=True).as_text())
            else:
                if not options["dry_run"]:
                    self._require_gate()
                report = postipoiss.auto_apply(write=not options["dry_run"])
                prefix = "PROOVIJOOKS, midagi ei salvestatud\n" if options["dry_run"] else ""
                self.stdout.write(prefix + report.as_text())
        except DomainError as error:
            raise CommandError(str(error)) from error

    def _package(self, options: dict[str, Any]) -> str:
        if not options["package"]:
            raise CommandError("--package on kohustuslik.")
        return str(options["package"])

    def _require_gate(self) -> None:
        if not settings.REAL_DATA_ALLOWED:
            raise CommandError(
                "REAL_DATA_ALLOWED is off in this environment. Postipoiss letters are real "
                "Koda correspondence and may only be loaded where the deployment says so."
            )

    def _status(self) -> None:
        self.stdout.write(f"kirju: {PostipoissLetter.objects.count()}")
        for row in (
            PostipoissPairing.objects.values("state", "tier")
            .annotate(n=Count("id"))
            .order_by("state", "tier")
        ):
            self.stdout.write(f"  {row['state']:<10} {row['tier']}  {row['n']}")
