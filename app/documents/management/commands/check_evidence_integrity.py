"""Is the evidence still there, and is it still what we hashed?

The question an operator has to be able to answer without a database session,
and the one no constraint in the schema can answer for them: evidence bytes live
outside PostgreSQL, so "this row points at an object that is not there" is
invisible to every check the database performs on itself.

Read-only, always. Nothing here repairs anything, and that is deliberate rather
than unfinished — a missing evidence object is a restore-from-backup decision,
and a checksum that disagrees is a question about which of the two copies is
the real one. Neither is a decision a management command should make at three in
the morning.

Two depths::

    manage.py check_evidence_integrity              # structural, cheap
    manage.py check_evidence_integrity --verify-sha # reads every stored byte

The structural pass is a handful of queries plus an existence and size check per
version. The deep pass hashes the whole store, which is a maintenance window on
a multi-gigabyte corpus, so it is never implied.

Exit 0 when nothing was found, 1 when something was. Safe to run from cron: the
output is aggregate counts plus UUIDs and storage keys, and never a document
title or a filename — in this corpus those are frequently the confidential part.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from app.documents.integrity import INTEGRITY_FAILURES, check_evidence

#: How many identifiers are printed per finding class before the rest are
#: counted instead. A store that has lost a thousand objects has one problem,
#: not a thousand, and a thousand lines of UUID buries the summary that says so.
DEFAULT_SAMPLE = 20


class Command(BaseCommand):
    help = "Check that every DocumentVersion's bytes exist, and report what does not."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--verify-sha",
            action="store_true",
            help="Read every stored object and check it against its recorded SHA-256.",
        )
        parser.add_argument(
            "--skip-storage-scan",
            action="store_true",
            help=(
                "Do not walk the store looking for unreferenced objects. "
                "Answers only 'has any row lost its bytes'."
            ),
        )
        parser.add_argument(
            "--sample",
            type=int,
            default=DEFAULT_SAMPLE,
            help=f"Identifiers printed per finding class (default {DEFAULT_SAMPLE}, 0 for all).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        report = check_evidence(
            verify_sha=options["verify_sha"],
            scan_storage=not options["skip_storage_scan"],
        )

        self.stdout.write(f"Versions checked:      {report.versions_checked}")
        if not options["skip_storage_scan"]:
            self.stdout.write(f"Stored objects seen:   {report.objects_seen}")
        if report.sha_verified:
            self.stdout.write(f"Bytes hashed:          {report.bytes_hashed}")
        else:
            self.stdout.write(
                "Checksums:             not verified (pass --verify-sha to read every object)"
            )

        if report.ok:
            self.stdout.write(self.style.SUCCESS("No integrity problems found."))
            return

        sample = options["sample"]
        grouped = report.by_kind()
        for kind in sorted(grouped):
            findings = grouped[kind]
            style = self.style.ERROR if kind in INTEGRITY_FAILURES else self.style.WARNING
            self.stdout.write(style(f"\n{kind}: {len(findings)}"))
            shown = findings if sample <= 0 else findings[:sample]
            for finding in shown:
                self.stdout.write(f"  {finding.subject}\t{finding.detail}")
            if len(findings) > len(shown):
                self.stdout.write(f"  … and {len(findings) - len(shown)} more")

        serious = sum(len(rows) for kind, rows in grouped.items() if kind in INTEGRITY_FAILURES)
        if serious:
            self.stdout.write(
                self.style.ERROR(
                    f"\n{serious} finding(s) mean evidence is not intact. Restore from backup "
                    "rather than editing rows to match what the store currently holds: a row "
                    "corrected to fit missing bytes turns a detected loss into an undetectable "
                    "one."
                )
            )
        raise SystemExit(1)
