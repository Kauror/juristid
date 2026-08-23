"""The opinions-archive reconciliation.

    manage.py opinion_archive audit    --opinions … [--kodadash …]
    manage.py opinion_archive plan     --opinions … [--kodadash …] [--report …]
    manage.py opinion_archive dry-run  --opinions … [--kodadash …]
    manage.py opinion_archive catalogue --opinions … [--kodadash …]
    manage.py opinion_archive apply    --opinions … [--kodadash …]
    manage.py opinion_archive materialize-plan --opinions … --expect-archive-sha256 …
    manage.py opinion_archive materialize      --opinions … --expect-archive-sha256 …
    manage.py opinion_archive content-plan
    manage.py opinion_archive content-apply
    manage.py opinion_archive supersede-plan
    manage.py opinion_archive supersede
    manage.py opinion_archive derive-links
    manage.py opinion_archive status
    manage.py opinion_archive verify

Separate commands rather than a ``--apply`` flag, for the reason the historical
importer already learned: the phases have different consequences and a flag is
easy to mistype. ``audit`` and ``plan`` write nothing and touch no database
rows. ``dry-run`` executes the real plan against the real schema and rolls the
*database* back. ``catalogue`` and ``apply`` commit, and both refuse unless the
sources still hash to what the plan was reviewed against (Stage-2H brief 47, 48,
49).

``catalogue`` records the archive, the producer's metadata and the
reconciliation's proposals, and stops there. It creates no Submission — not even
for a proposal whose match class an apply would execute without asking anyone.
It exists because ``materialize`` needs a catalogue to work from and the only
thing that used to produce one was the full ``apply``, so holding a letter's
bytes meant first asserting who sent it. The production sequence is
``plan`` → ``catalogue`` → ``materialize`` → search → review → ``apply``
(docs/production-readiness.md).

``materialize`` is deliberately not part of ``apply``. Holding a letter's bytes
and deciding whose letter it is are different acts with different bars, and
tying them together is what left two thirds of the corpus visible only as
catalogue rows. It creates no Submission and links no Matter
(app/legacy_import/opinion_materialize.py).

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

#: Written to ``OpinionArchiveBatch.notes`` so the batch list says what the run
#: did. A catalogue batch is already identifiable by having no
#: ``OpinionSubmissionImport`` pointing at it, but reading a phase off an absence
#: is the kind of inference an operator should not have to make.
CATALOGUE_BATCH_NOTE = "catalogue: kataloogimine ilma kanoonilise arvamuseta"


class Command(BaseCommand):
    help = "Reconcile the Chamber opinions archive against the register, OneNote and KodaDash."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "phase",
            choices=[
                "audit",
                "plan",
                "dry-run",
                "catalogue",
                "apply",
                "materialize-plan",
                "materialize",
                "content-plan",
                "content-apply",
                "supersede-plan",
                "supersede",
                "derive-links",
                "status",
                "verify",
            ],
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
        if phase in {"materialize-plan", "materialize"}:
            return self._materialize(phase, options)
        if phase in {"content-plan", "content-apply"}:
            return self._content(phase)
        if phase in {"supersede-plan", "supersede"}:
            return self._supersede(phase)
        if phase == "derive-links":
            return self._derive_links()

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
        if phase == "catalogue":
            return self._catalogue(plan, options)
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

    def _catalogue(self, plan: Any, options: dict) -> None:
        """Record the archive and the reconciliation. Assert nothing about sending.

        The source pin is checked before the batch is opened, so a mismatched
        archive leaves no run behind at all — a refused catalogue should not be
        distinguishable from one that never started.
        """
        from app.legacy_import.opinion_apply import (
            OpinionApplyError,
            catalogue_plan,
            open_batch,
            require_unchanged_sources,
        )

        try:
            require_unchanged_sources(plan)
        except OpinionApplyError as error:
            raise CommandError(str(error)) from error

        batch = open_batch(plan, notes=CATALOGUE_BATCH_NOTE)
        report = catalogue_plan(plan, batch=batch)
        batch.finished_at = timezone.now()
        batch.save(update_fields=["finished_at", "updated_at"])

        self.stdout.write(report.as_text())
        self.stdout.write(
            self.style.SUCCESS("\nArhiiv on kataloogitud. Kanoonilist arvamust ei loodud.")
        )
        self.stdout.write(
            "Järgmine ohutu samm: opinion_archive materialize-plan "
            "--opinions … --expect-archive-sha256 …\n"
            "Ülevaatuse töölaud avaneb alles pärast materialiseerimist ja "
            "tuvastatud kasutajaga sisselogimist; kanooniline apply on eraldi samm."
        )
        if options.get("report"):
            self._write_report(options["report"], report.__dict__ | {"batch_id": str(batch.pk)})

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

    def _materialize(self, phase: str, options: dict) -> None:
        """Copy the archive's bytes into evidence storage, or say what that would do.

        Both phases pin the archive by SHA-256. `materialize-plan` writes
        nothing at all — not even a batch row — so an operator can look at the
        arithmetic, and at anything already broken, before deciding.
        """
        from app.legacy_import.opinion_materialize import (
            OpinionMaterializeError,
            materialize,
            plan_materialization,
        )

        archive = options.get("opinions")
        if archive is None:
            raise CommandError("No opinions archive. Pass --opinions.")
        if phase == "materialize":
            self._require_gate()

        run = plan_materialization if phase == "materialize-plan" else materialize
        try:
            report = run(
                archive_path=Path(archive),
                expected_archive_sha256=options["expect_archive_sha256"],
            )
        except OpinionMaterializeError as error:
            raise CommandError(str(error)) from error

        self.stdout.write(report.as_text())
        if options.get("report"):
            self._write_report(options["report"], report.__dict__)
        if not report.ok:
            # A non-zero exit, because "some bytes are missing" is the one
            # outcome an operator must not scroll past on the way to the next
            # step in the runbook.
            raise CommandError("Materialiseerimine leidis puuduvaid või mittevastavaid baite.")
        if phase == "materialize":
            self.stdout.write(
                self.style.SUCCESS(
                    "\nArhiivi baidid on hoiul. Otsinguprojektsiooni uuendamiseks kasuta "
                    "`opinion_archive_search rebuild`."
                )
            )

    def _content(self, phase: str) -> None:
        """The second pass: read the letters and propose from their own text.

        Reads the database and the extracted text already held; takes no source
        path and no SHA. `content-plan` writes nothing at all rather than
        writing and rolling back, because there is nothing to exercise — the
        pass only ever inserts queue rows.

        Nothing it proposes is automatic. Every row lands in the queue in
        `CONTENT_MULTI_SIGNAL`, which is deliberately outside
        ``AUTOMATIC_MATCH_CLASSES`` until somebody has measured this pass
        against the real corpus (docs/adr/0023).
        """
        from app.legacy_import.opinion_content_match import (
            apply_content_matches,
            plan_content_matches,
        )

        run = plan_content_matches if phase == "content-plan" else apply_content_matches
        report = run()
        self.stdout.write(report.as_text())
        if phase == "content-plan":
            self.stdout.write("\nMidagi ei salvestatud.")
        else:
            self.stdout.write(
                "\nEttepanekud on ülevaatuse järjekorras. Ükski neist ei loonud arvamust."
            )

    def _supersede(self, phase: str) -> None:
        """Retire pending proposals a later run has already answered.

        No source and no SHA: this reads the database and nothing else. The
        plan phase rolls its own work back, so its counts are the counts the
        real phase would produce rather than an estimate of them.
        """
        from app.legacy_import.opinion_supersede import sweep_superseded

        report = sweep_superseded(dry_run=phase == "supersede-plan")
        self.stdout.write(report.as_text())
        if phase == "supersede-plan":
            self.stdout.write("\nMidagi ei salvestatud.")

    def _derive_links(self) -> None:
        """Record the archive-to-Matter relationships that already follow.

        Only from exact identity — the automatic match classes and existing
        Submissions. Nothing here is a resemblance, nothing is removed, and
        running it twice changes nothing.
        """
        from app.legacy_import.opinion_links import derive_links

        report = derive_links()
        self.stdout.write(report.as_text())

    def _status(self) -> None:
        from app.legacy_import.opinion_archive import (
            OpinionArchiveItem,
            OpinionArchiveMetadata,
            OpinionMatchCandidate,
            OpinionSubmissionImport,
        )
        from app.legacy_import.opinion_binary import OpinionArchiveMatterLink
        from app.legacy_import.opinion_enums import (
            HUMAN_DECIDED_STATES,
            OpinionCandidateState,
        )

        # Three disjoint numbers that together account for every candidate:
        # what the importer still owns, what it finished, and what a person
        # answered. Printing only "ülevaatust ootel" made a finished row and a
        # rejected one indistinguishable from a queue nobody had started.
        rows = [
            ("arhiivikirjeid", OpinionArchiveItem.objects.count()),
            ("erinevaid baite", OpinionArchiveItem.objects.values("sha256").distinct().count()),
            ("tuletatud metaandmeid", OpinionArchiveMetadata.objects.count()),
            ("sidumiskandidaate", OpinionMatchCandidate.objects.count()),
            (
                "ülevaatust ootel",
                OpinionMatchCandidate.objects.filter(state=OpinionCandidateState.PENDING).count(),
            ),
            (
                "rakendatud",
                OpinionMatchCandidate.objects.filter(state=OpinionCandidateState.APPLIED).count(),
            ),
            (
                "ülevaataja otsustatud",
                OpinionMatchCandidate.objects.filter(state__in=HUMAN_DECIDED_STATES).count(),
            ),
            (
                "asendatud",
                OpinionMatchCandidate.objects.filter(
                    state=OpinionCandidateState.SUPERSEDED
                ).count(),
            ),
            ("teemaseoseid", OpinionArchiveMatterLink.objects.count()),
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
        from django.db import models
        from django.db.models import Count

        from app.legacy_import.opinion_archive import (
            OpinionArchiveItem,
            OpinionMatchCandidate,
            OpinionSubmissionImport,
        )
        from app.legacy_import.opinion_enums import OpinionCandidateState
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

        # The provenance chain has to describe one decision end to end: this
        # file → this candidate → this Matter → this Submission. A row that
        # names candidate A while its Submission sits on candidate A's *other*
        # Matter is not a small inconsistency, it is an explanation that would
        # mislead the next person who trusts it (brief 46; this task, 19).
        crossed_item = (
            OpinionSubmissionImport.objects.filter(candidate__isnull=False)
            .exclude(candidate__item_id=models.F("item_id"))
            .count()
        )
        if crossed_item:
            problems.append(f"{crossed_item} arhiiviimpordil viitab kandidaat teisele failile")

        crossed_matter = (
            OpinionSubmissionImport.objects.filter(candidate__matter__isnull=False)
            .exclude(candidate__matter_id=models.F("submission__matter_id"))
            .count()
        )
        if crossed_matter:
            problems.append(f"{crossed_matter} arhiiviimpordil on kandidaadil teine teema")

        # APPLIED means "this produced a canonical Submission". A row claiming
        # it without an import naming it is a candidate that left the queue
        # without leaving a record — the failure mode the state exists to make
        # impossible.
        unbacked = (
            OpinionMatchCandidate.objects.filter(state=OpinionCandidateState.APPLIED)
            .filter(submission_imports__isnull=True)
            .count()
        )
        if unbacked:
            problems.append(f"{unbacked} rakendatud kandidaadil ei ole ühtegi arhiiviimporti")

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

        # Supersession and multi-Matter links are checked here rather than in
        # their own command, because an operator who runs one verify and reads
        # "all checks passed" has been told something about the whole import.
        from app.legacy_import.opinion_links import link_findings
        from app.legacy_import.opinion_supersede import superseded_findings

        problems.extend(superseded_findings())
        problems.extend(link_findings())

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
