"""Read-only, independent parity check of Lovable's values against Juristid (r98, ADR 0238).

    manage.py lovable_parity                       # the whole cohort, from the last stored reading
    manage.py lovable_parity --sample 30 --seed X  # a reproducible sample of it
    manage.py lovable_parity --record              # also keep counts and (reference, field) pairs
    manage.py lovable_parity --fresh [--sample N]  # take a NEW reading of Lovable first (operator)

The default reads the newest SCHEDULED reading the nightly cycle stored, so it makes **no** request
to Lovable and adds no traffic. ``--fresh`` is the explicit, operator-only second mode: it takes one
new consistent read-only reading through the synchronisation's own allowlisted access
(`app.legacy_import.parity_fresh`), bounded and retried a bounded number of times; nothing starts
it but this command. Neither mode writes to Lovable or to a Teema.

Exit code 0: nothing wrong and nothing unverified; 1: a mismatch, a held closure, an unverified
Teema, an unexplained source change or a comparison that could not be made; 2: nothing to compare.
A clean SAMPLE exits 0 too but prints that it is not register parity.
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
    help = "Independent, read-only parity check against Lovable (stored reading, or --fresh)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--sample", type=int, default=0, help="compare only this many Teemad")
        parser.add_argument("--seed", default="juristid", help="the sample's seed (reproducible)")
        parser.add_argument("--record", action="store_true", help="keep the counts for Haldus")
        parser.add_argument(
            "--fresh",
            action="store_true",
            help="take a new read-only reading of Lovable (operator-triggered; always recorded)",
        )
        parser.add_argument("--credentials", default="", help="credentials file (default: env)")

    def handle(self, *args: Any, **options: Any) -> None:
        if options["fresh"]:
            self._fresh(options)
            return
        run = (
            LovableSyncRun.objects.filter(trigger=SyncTrigger.SCHEDULED, snapshot__isnull=False)
            .order_by("-started_at")
            .first()
        )
        snapshot = LovableSyncSnapshot.objects.filter(run=run).first() if run else None
        if snapshot is None or run is None:
            self.stderr.write("no stored reading to compare")
            raise SystemExit(2)
        view = verifier.view_from_snapshot(snapshot.document, snapshot.taken_at, snapshot.sha256)
        references = None
        mode = "full"
        population = sorted(str(r["nr"]) for r in view.rows.values() if r.get("operatiivne_ulatus"))
        if options["sample"]:
            references = verifier.sample_of(population, options["sample"], options["seed"])
            mode = "sample"
        now = timezone.now()
        report = verifier.verify(
            view,
            today=now.astimezone(ZoneInfo("Europe/Tallinn")).date(),
            references=references,
            mode=mode,
        )
        report.eligible = len(population)
        report.sample_size = options["sample"] or None
        report.seed = options["seed"] if options["sample"] else ""
        self._print(report)
        if options["record"]:
            LovableParityReport.objects.create(
                at=now,
                mode=mode,
                source="snapshot",
                snapshot_taken_at=report.taken_at,
                snapshot_sha256=report.sha256,
                run_key=run.run_key,
                teemad=report.teemad,
                sample_size=report.sample_size,
                eligible=report.eligible,
                seed=report.seed,
                counts=report.counts,
                mismatches=[list(p) for p in report.mismatches],
                held=[list(p) for p in report.held],
                unverified=[list(p) for p in report.unverified],
            )
        raise SystemExit(0 if report.clean else 1)

    def _fresh(self, options: dict[str, Any]) -> None:
        from app.legacy_import import lovable_source, parity_fresh

        try:
            config = lovable_source.load_config(options["credentials"] or None)
            transport = lovable_source.HttpTransport(config)
        except lovable_source.SourceError as error:
            self.stderr.write(f"cannot read Lovable: {error.code}")
            raise SystemExit(2) from error
        report = parity_fresh.run_fresh(
            transport, now=timezone.now, sample=options["sample"], seed=options["seed"]
        )
        self._print(report)
        raise SystemExit(0 if report.clean else 1)

    def _print(self, report: verifier.Report) -> None:
        counts = ", ".join(f"{k}={report.counts.get(k, 0)}" for k in verifier.CLASSES)
        if report.error:
            self.stdout.write(f"COMPARISON NOT MADE ({report.error}): {counts}")
            return
        taken = f"{report.taken_at:%Y-%m-%d %H:%M}" if report.taken_at else "?"
        self.stdout.write(
            f"{report.source} reading {taken} sha {report.sha256[:12]}: "
            f"{report.teemad} Teemad, {report.fields} field comparisons: {counts}"
        )
        if report.mode == "sample":
            self.stdout.write(
                f"SAMPLE of {report.sample_size} from {report.eligible}: a clean sample is not "
                "register parity."
            )
        for kind, references in sorted(report.source_changes.items()):
            self.stdout.write(f"  source {kind}: {len(references)} since the last synchronisation")
        for name, pairs in (
            ("MISMATCH", report.mismatches),
            ("HELD", report.held),
            ("UNVERIFIED", report.unverified),
            ("CHANGED_SINCE_SYNC", report.changed_since_sync),
        ):
            for reference, field_name in pairs:
                self.stdout.write(f"  {name} {reference} {field_name}")
