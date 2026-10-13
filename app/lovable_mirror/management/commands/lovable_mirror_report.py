"""Print the reconciliation preview for one named reader. Reads only.

    python manage.py lovable_mirror_report --as ADMIN_UPN
    python manage.py lovable_mirror_report --as ADMIN_UPN --json

Matching runs over the Matters the named person may read, exactly as the page
does, so ``--as`` is required: a report computed for nobody in particular would
be computed over a population nobody reads (CONTRACT §3). Only a person who may
open the preview page may be named.

The JSON form names Matter references and Lovable titles. It is real register
content: print it to the terminal of the approved environment, never into a
file under the repository, a ticket, a screenshot or a pull request.
"""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.accounts.models import User
from app.lovable_mirror.reconcile import (
    ReconciliationReport,
    RowChange,
    RowReconciliation,
    build_report,
)
from app.lovable_mirror.views import may_view_mirror


def _row(item: RowReconciliation) -> dict[str, Any]:
    return {
        "nr": item.row.key,
        "class": item.match_class,
        "reason": item.reason,
        "matter": item.matter.display_reference if item.matter else None,
        "conflicts": [
            {"field": c.field, "lovable": c.source, "juristid": c.canonical} for c in item.conflicts
        ],
    }


def _change(item: RowChange) -> dict[str, Any]:
    return {
        "nr": item.key,
        "fields": [
            {"field": f.field, "class": f.mapping_class, "before": f.before, "after": f.after}
            for f in item.fields
        ],
    }


def as_dict(report: ReconciliationReport) -> dict[str, Any]:
    coverage = report.coverage
    return {
        "snapshot": report.snapshot.manifest_sha256,
        "exported_at": report.snapshot.source_exported_at.isoformat(),
        "previous": report.previous.manifest_sha256 if report.previous else None,
        "added": [_change(c) for c in report.diff.added],
        "removed": [_change(c) for c in report.diff.removed],
        "changed": [_change(c) for c in report.diff.changed],
        "matched": [_row(r) for r in report.matched],
        "unmatched": [_row(r) for r in report.unmatched],
        "ambiguous": [_row(r) for r in report.ambiguous],
        "conflicts": [_row(r) for r in report.conflicts],
        "coverage": {
            "tables_present": coverage.tables_present,
            "tables_absent": coverage.tables_absent,
            "unverified_tables": list(coverage.unverified_tables),
            "uncatalogued_fields": coverage.uncatalogued_fields,
            "dataset_boundary": coverage.dataset_boundary,
            "previous_boundary": coverage.previous_boundary,
            "unkeyed_rows": coverage.unkeyed_rows,
            "outcome_counts": coverage.outcome_counts,
        },
    }


class Command(BaseCommand):
    help = "Report how the latest Lovable snapshot compares. Writes nothing."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--as", dest="upn", required=True, help="UPN of the reader.")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        user = User.objects.filter(upn__iexact=str(options["upn"]).strip()).first()
        if user is None or not may_view_mirror(user):
            raise CommandError("See kasutaja ei tohi Lovable'i peeglit vaadata.")
        report = build_report(user)
        if report is None:
            raise CommandError("Ühtki Lovable'i hetktõmmist ei ole imporditud.")
        data = as_dict(report)
        if options["json"]:
            self.stdout.write(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
            return
        write = self.stdout.write
        write(f"Hetktõmmis      {data['snapshot']}  ({data['exported_at']})")
        write(f"Eelmine         {data['previous'] or '—'}")
        for key, label in (
            ("added", "lisandunud"),
            ("removed", "kadunud"),
            ("changed", "muutunud"),
            ("matched", "seotud"),
            ("unmatched", "sidumata"),
            ("ambiguous", "mitmeti"),
            ("conflicts", "vastuolus"),
        ):
            write(f"  {label:<14} {len(data[key])}")
        for note in (data["coverage"]["dataset_boundary"], data["coverage"]["previous_boundary"]):
            if note:
                write(self.style.WARNING(f"  {note}"))
        write(self.style.SUCCESS("Andmebaasi ei kirjutatud."))
