"""Read-only, independent parity check of Lovable's values against Juristid (r98, ADR 0238).

    manage.py lovable_parity                       # the whole cohort, from the last stored reading
    manage.py lovable_parity --sample 30 --seed X  # a reproducible sample
    manage.py lovable_parity --record              # also keep counts and (reference, field) pairs

It reads the newest SCHEDULED reading the nightly cycle stored, so it makes **no** request to
Lovable and adds no traffic; it never writes to Lovable or to a Teema. Exit code 0: nothing wrong
and nothing unverified; 1: a mismatch or an unverified Teema; 2: no reading to compare.
"""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand
from django.utils import timezone

from app.legacy_import.lovable_sync_models import (
    LovableParityReport,
    LovableSyncRun,
    LovableSyncSnapshot,
    SyncTrigger,
)
from app.legacy_import.parity import verifier


class Command(BaseCommand):
    help = "Independent, read-only parity check against the last stored Lovable reading."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--sample", type=int, default=0, help="compare only this many Teemad")
        parser.add_argument("--seed", default="juristid", help="the sample's seed (reproducible)")
        parser.add_argument("--record", action="store_true", help="keep the counts for Haldus")

    def handle(self, *args: Any, **options: Any) -> None:
        run = (
            LovableSyncRun.objects.filter(trigger=SyncTrigger.SCHEDULED, snapshot__isnull=False)
            .order_by("-started_at")
            .first()
        )
        snapshot = LovableSyncSnapshot.objects.filter(run=run).first() if run else None
        if snapshot is None:
            self.stderr.write("no stored reading to compare")
            raise SystemExit(2)
        view = verifier.view_from_snapshot(snapshot.document, snapshot.taken_at, snapshot.sha256)
        references = None
        mode = "full"
        if options["sample"]:
            population = sorted(
                str(r["nr"]) for r in view.rows.values() if r.get("operatiivne_ulatus")
            )
            references = verifier.sample_of(population, options["sample"], options["seed"])
            mode = "sample"
        now = timezone.now()
        report = verifier.verify(
            view,
            today=now.astimezone(ZoneInfo("Europe/Tallinn")).date(),
            references=references,
            mode=mode,
        )
        counts = ", ".join(f"{k}={report.counts.get(k, 0)}" for k in verifier.CLASSES)
        self.stdout.write(
            f"reading {report.taken_at:%Y-%m-%d %H:%M} sha {report.sha256[:12]}: "
            f"{report.teemad} Teemad, {report.fields} field comparisons: {counts}"
        )
        for name, pairs in (("MISMATCH", report.mismatches), ("UNVERIFIED", report.unverified)):
            for reference, field_name in pairs:
                self.stdout.write(f"  {name} {reference} {field_name}")
        if options["record"]:
            LovableParityReport.objects.create(
                at=now,
                mode=mode,
                snapshot_taken_at=report.taken_at,
                snapshot_sha256=report.sha256,
                teemad=report.teemad,
                counts=report.counts,
                mismatches=[list(p) for p in report.mismatches],
                held=[list(p) for p in report.held],
                unverified=[list(p) for p in report.unverified],
            )
        raise SystemExit(0 if report.clean else 1)
