"""Making the held archive searchable, and checking that it still is.

    manage.py opinion_archive_search extract-text [--force]
    manage.py opinion_archive_search rebuild      [--force]
    manage.py opinion_archive_search status
    manage.py opinion_archive_search verify

Separate from ``opinion_archive`` because everything here is **derived**. The
other command holds bytes and decides whose letter is whose; this one reads what
is already held and writes rows that can be thrown away and rebuilt. Keeping the
two apart is what lets an operator run ``rebuild`` on a Friday afternoon without
consulting a runbook, and it is why none of these phases takes a source path or
a SHA-256: there is no source to pin, only the database.

``verify`` is the one that earns its place in a runbook. A projection nobody
checks drifts silently — a binary materialised after the last rebuild is simply
absent from every search, and absence is the failure mode that does not announce
itself. It exits non-zero when it finds drift, so a scheduled run is a monitor
rather than a page of text nobody reads.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Extract text from held archive binaries and maintain the archive search projection."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("phase", choices=["extract-text", "rebuild", "status", "verify"])
        parser.add_argument(
            "--force",
            action="store_true",
            help="Redo work that is already current, rather than skipping it.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        phase = options["phase"]
        if phase == "extract-text":
            return self._extract(force=options["force"])
        if phase == "rebuild":
            return self._rebuild(force=options["force"])
        if phase == "status":
            return self._status()
        return self._verify()

    # -- phases ------------------------------------------------------------

    def _extract(self, *, force: bool) -> None:
        from app.legacy_import.opinion_text import extract_all, extraction_is_permitted

        if not extraction_is_permitted():
            # Not an error and not a refusal to run: the run is what records
            # BLOCKED against every binary, which is how the coverage figures
            # tell "we chose not to open these" apart from "we have not tried".
            self.stdout.write(
                self.style.WARNING(
                    "Reaalandmete keskkond: skaneerimata faile ei avata (docs/adr/0014).\n"
                    "Iga bait märgitakse BLOCKED; arhiiv jääb metaandmete järgi otsitavaks."
                )
            )
        report = extract_all(force=force)
        self.stdout.write(report.as_text())
        self.stdout.write("\nProjektsiooni uuendamiseks: `opinion_archive_search rebuild`.")

    def _rebuild(self, *, force: bool) -> None:
        from app.legacy_import.opinion_search import rebuild_archive_index

        report = rebuild_archive_index(force=force)
        self.stdout.write(report.as_text())

    def _status(self) -> None:
        from app.legacy_import.opinion_binary import OpinionArchiveBinary, OpinionArchiveText
        from app.legacy_import.opinion_enums import ArchiveTextState
        from app.legacy_import.opinion_search import unindexed_binaries
        from app.legacy_import.opinion_search_models import OpinionArchiveSearchDocument

        held = OpinionArchiveBinary.objects.count()
        indexed = OpinionArchiveSearchDocument.objects.count()
        rows = [
            ("hoitud baite", held),
            ("otsinguridu", indexed),
            ("indekseerimata", unindexed_binaries().count()),
            (
                "sisuga ridu",
                OpinionArchiveSearchDocument.objects.filter(has_body_text=True).count(),
            ),
        ]
        for state, label in ArchiveTextState.choices:
            rows.append((f"tekst: {label}", OpinionArchiveText.objects.filter(state=state).count()))
        for label, value in rows:
            self.stdout.write(f"  {label:<38} {value:>10}")

    def _verify(self) -> None:
        """Say whether the projection still describes what is held.

        Every check below compares derived rows against canonical ones and
        never the other way round. Nothing here repairs anything: a verify that
        quietly fixed what it found would make the next run's clean result
        meaningless.
        """
        from app.legacy_import.opinion_search import archive_index_findings

        findings = archive_index_findings()
        if not findings:
            self.stdout.write(self.style.SUCCESS("Arhiivi otsinguprojektsioon on kooskõlas."))
            return
        for finding in findings:
            self.stdout.write(f"  leid: {finding}")
        raise CommandError(
            f"Otsinguprojektsioon ei vasta hoitud baitidele ({len(findings)} leidu). "
            "Paranda `opinion_archive_search rebuild --force`."
        )
