"""The opinions-archive reconciliation.

    manage.py opinion_archive audit    --opinions … [--kodadash …]
    manage.py opinion_archive plan     --opinions … [--kodadash …] [--report …]
    manage.py opinion_archive dry-run  --opinions … [--kodadash …]
    manage.py opinion_archive apply    --opinions … [--kodadash …]
    manage.py opinion_archive status
    manage.py opinion_archive verify

Separate commands rather than a ``--apply`` flag, for the reason the historical
importer already learned: the phases have different consequences and a flag is
easy to mistype. ``audit`` and ``plan`` write nothing and touch no database
rows. ``dry-run`` executes the real plan against the real schema and rolls the
*database* back. Only ``apply`` commits, and it refuses unless the sources still
hash to what the plan was reviewed against (Stage-2H brief 47, 48, 49).

**The rollback is database-only.** ``add_evidence_version`` writes bytes to the
evidence store, and the filesystem does not join the transaction: a dry-run that
reaches the evidence stage can leave stored objects behind even though every row
disappears. They are unreferenced rather than wrong, but "nothing was written"
would be an overstatement. For a real import prefer ``plan`` → ``apply``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone


class Command(BaseCommand):
    help = "Reconcile the Chamber opinions archive against the register, OneNote and KodaDash."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "phase", choices=["audit", "plan", "dry-run", "apply", "status", "verify"]
        )
        parser.add_argument("--opinions", type=Path, help="Opinions.zip or an opinions/ folder")
        parser.add_argument("--kodadash", type=Path, help="KodaDash opinion workbook")
        parser.add_argument("--expect-archive-sha256", default="")
        parser.add_argument("--expect-kodadash-sha256", default="")
        parser.add_argument("--report", type=Path, help="Write a JSON summary here.")

    def handle(self, *args: Any, **options: Any) -> None:
        phase = options["phase"]
        if phase == "status":
            return self._status()
        if phase == "verify":
            return self._verify()

        plan = self._build(options)
        if phase in {"audit", "plan"}:
            self.stdout.write(plan.as_text())
            for finding in plan.findings:
                self.stdout.write(f"  leid: {finding}")
            if options.get("report"):
                self._write_report(options["report"], plan.summary())
            return None

        self._require_gate()
        if phase == "dry-run":
            return self._dry_run(plan, options)
        return self._apply(plan, options)

    # -- phases ------------------------------------------------------------

    def _build(self, options: dict) -> Any:
        from app.legacy_import.opinion_plan import OpinionPlanError, build_plan
        from app.legacy_import.opinion_sources import OpinionSourceError

        archive = options.get("opinions")
        if archive is None:
            raise CommandError("No opinions archive. Pass --opinions.")
        try:
            return build_plan(
                archive_path=Path(archive),
                kodadash_path=Path(options["kodadash"]) if options.get("kodadash") else None,
                expected_archive_sha256=options["expect_archive_sha256"],
                expected_kodadash_sha256=options["expect_kodadash_sha256"],
            )
        except (OpinionPlanError, OpinionSourceError) as error:
            raise CommandError(str(error)) from error

    def _require_gate(self) -> None:
        if not settings.REAL_DATA_ALLOWED:
            raise CommandError(
                "REAL_DATA_ALLOWED is off in this environment. The opinions archive is real "
                "Koda correspondence and may only be imported where the deployment says so."
            )

    def _dry_run(self, plan: Any, options: dict) -> None:
        """The real plan, against the real schema, rolled back.

        Not a simulation. A dry run that exercised different code would agree
        with apply right up to the day it mattered.
        """
        from app.legacy_import.opinion_apply import (
            apply_plan,
            open_batch,
            require_unchanged_sources,
        )

        require_unchanged_sources(plan)
        self.stdout.write("Proovikäik — andmebaasi muudatused keeratakse tagasi.\n")
        self.stdout.write(
            self.style.WARNING(
                "Tähelepanu: tagasikeeramine hõlmab ainult andmebaasi. Kui proovikäik jõuab "
                "tõendifailide salvestamiseni, võivad need failid salvestusse alles jääda — "
                "failisüsteem ei osale transaktsioonis. Päris impordiks kasuta plan → apply."
            )
        )

        class _Rollback(Exception):
            pass

        carrier: dict[str, Any] = {}
        try:
            with transaction.atomic():
                batch = open_batch(plan)
                carrier["report"] = apply_plan(plan, batch=batch)
                raise _Rollback
        except _Rollback:
            pass

        self.stdout.write(carrier["report"].as_text())
        self.stdout.write(
            self.style.SUCCESS("\nAndmebaasi muudatused keerati tagasi (failisalvestus mitte).")
        )
        if options.get("report"):
            self._write_report(
                options["report"], {"dry_run": carrier["report"].__dict__, "plan": plan.summary()}
            )

    def _apply(self, plan: Any, options: dict) -> None:
        from app.legacy_import.opinion_apply import (
            OpinionApplyError,
            apply_plan,
            open_batch,
            require_unchanged_sources,
        )

        try:
            require_unchanged_sources(plan)
        except OpinionApplyError as error:
            raise CommandError(str(error)) from error

        batch = open_batch(plan)
        report = apply_plan(plan, batch=batch)
        batch.finished_at = timezone.now()
        batch.save(update_fields=["finished_at", "updated_at"])

        self.stdout.write(report.as_text())
        self.stdout.write(
            self.style.SUCCESS(
                "\nArhiiv on kataloogitud. Ülevaatust vajav osa on /haldus/arvamuste-ulevaatus/."
            )
        )
        if options.get("report"):
            self._write_report(options["report"], report.__dict__ | {"batch_id": str(batch.pk)})

    def _status(self) -> None:
        from app.legacy_import.opinion_archive import (
            OpinionArchiveItem,
            OpinionArchiveMetadata,
            OpinionMatchCandidate,
            OpinionSubmissionImport,
        )
        from app.legacy_import.opinion_enums import OpinionCandidateState

        rows = [
            ("arhiivikirjeid", OpinionArchiveItem.objects.count()),
            ("erinevaid baite", OpinionArchiveItem.objects.values("sha256").distinct().count()),
            ("tuletatud metaandmeid", OpinionArchiveMetadata.objects.count()),
            ("sidumiskandidaate", OpinionMatchCandidate.objects.count()),
            (
                "ülevaatust ootel",
                OpinionMatchCandidate.objects.filter(state=OpinionCandidateState.PENDING).count(),
            ),
            ("arhiivist arvamusi", OpinionSubmissionImport.objects.count()),
            (
                "neist siin loodud",
                OpinionSubmissionImport.objects.filter(created_submission=True).count(),
            ),
        ]
        for label, value in rows:
            self.stdout.write(f"  {label:<24} {value:>8,}")

    def _verify(self) -> None:
        """Check what the import claims against what the database holds."""
        from django.db.models import Count

        from app.legacy_import.opinion_archive import (
            OpinionArchiveItem,
            OpinionSubmissionImport,
        )
        from app.submissions.enums import SentAtPrecision, SubmissionStatus

        problems: list[str] = []

        duplicated = (
            OpinionArchiveItem.objects.values("archive_sha256", "archive_relative_path")
            .annotate(n=Count("pk"))
            .filter(n__gt=1)
            .count()
        )
        if duplicated:
            problems.append(f"{duplicated} arhiivikirjet on kataloogitud rohkem kui korra")

        mismatched = 0
        for record in OpinionSubmissionImport.objects.select_related("item", "document_version"):
            version = record.document_version
            if version is not None and version.sha256 != record.item.sha256:
                mismatched += 1
        if mismatched:
            problems.append(f"{mismatched} arvamusel erineb tõendi SHA-256 arhiivi omast")

        # A historical Submission whose date came from the register must say so.
        # A DATE-precision row rendered as a timestamp is the defect this field
        # exists to prevent.
        wrong_precision = OpinionSubmissionImport.objects.filter(
            created_submission=True,
            submission__sent_at_precision=SentAtPrecision.TIMESTAMP,
        ).count()
        if wrong_precision:
            problems.append(
                f"{wrong_precision} taastatud arvamust väidab kellaaja täpsust, "
                "mida allikas ei andnud"
            )

        unevidenced = OpinionSubmissionImport.objects.filter(
            submission__status=SubmissionStatus.SENT, submission__final_version__isnull=True
        ).count()
        if unevidenced:
            problems.append(f"{unevidenced} saadetud arvamust ilma lõpliku tõendita")

        doubled = (
            OpinionSubmissionImport.objects.values("submission_id")
            .annotate(n=Count("pk"))
            .filter(n__gt=1)
            .count()
        )
        if doubled:
            self.stdout.write(
                f"  {doubled} arvamusel on mitu arhiivi esinemist — see on lubatud: "
                "sama kiri võib arhiivis olla mitu korda."
            )

        if problems:
            for problem in problems:
                self.stdout.write(self.style.ERROR(f"  {problem}"))
            raise CommandError(f"{len(problems)} kontrolli viga.")
        self.stdout.write(self.style.SUCCESS("  kõik kontrollid läbitud"))

    def _write_report(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        self.stdout.write(f"\nAruanne kirjutatud: {path}")
