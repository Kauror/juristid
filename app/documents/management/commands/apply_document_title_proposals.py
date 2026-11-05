"""Dry-run, or apply, a reviewed plan of document display titles.

    python manage.py apply_document_title_proposals --plan PLAN.jsonl \\
        --only-approved --actor UPN
    python manage.py apply_document_title_proposals --plan PLAN.jsonl \\
        --only-approved --actor UPN --apply --expect-plan-sha256 DIGEST

A dry run writes nothing and prints the plan's digest. ``--apply`` needs that
digest, so what is written is the file somebody reviewed. Every rename goes
through `rename_document` — the `Muuda nime` service — and is audited there as
`DOCUMENT_TITLE_CHANGED`. Only `Document.title` changes; the evidence filename,
bytes, checksum and versions stay as they are. Rows whose title moved since the
plan, rows on a closed Matter and removed documents are refused, not forced.
See `app/documents/title_proposals.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.documents.title_proposals import OUTCOMES, TitlePlanError, run_plan


class Command(BaseCommand):
    help = (
        "Dry-run (default) or apply reviewed document display-title proposals through "
        "the Muuda nime service; only APPROVE / EXPLICIT_TITLE rows, never the "
        "evidence filename."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--plan", required=True, help="JSON Lines plan file.")
        parser.add_argument(
            "--actor", required=True, help="The user (UPN) the renames are recorded for."
        )
        parser.add_argument(
            "--only-approved",
            action="store_true",
            help="Required: apply only rows recommended APPROVE with an explicit title.",
        )
        parser.add_argument("--apply", action="store_true", help="Write; without it, a dry run.")
        parser.add_argument(
            "--expect-plan-sha256",
            default="",
            help="Required by --apply: the digest printed by the reviewed dry run.",
        )
        parser.add_argument("--show-rows", action="store_true", help="Print one line per plan row.")

    def handle(self, *args: Any, **options: Any) -> None:
        if not options["only_approved"]:
            raise CommandError(
                "--only-approved is required: no other selection of plan rows exists."
            )
        path = Path(options["plan"])
        try:
            report = run_plan(
                path,
                actor_identifier=options["actor"],
                apply=bool(options["apply"]),
                expect_plan_sha256=options["expect_plan_sha256"],
            )
        except TitlePlanError as error:
            raise CommandError(str(error)) from error

        mode = "APPLIED" if report.applied else "DRY RUN (nothing written)"
        self.stdout.write(self.style.MIGRATE_HEADING(f"Document title proposals — {mode}"))
        self.stdout.write(f"  plan          {path.name}")
        self.stdout.write(f"  plan digest   {report.plan_sha256}")
        self.stdout.write(f"  rows          {len(report.results)}")
        counts = report.counts
        for name in OUTCOMES:
            if counts[name]:
                self.stdout.write(f"  {name:<24} {counts[name]}")
        if options["show_rows"]:
            self.stdout.write("")
            for result in report.results:
                detail = f"  {result.detail}" if result.detail else ""
                self.stdout.write(
                    f"  line {result.line:>5}  {result.document_id}  {result.outcome}{detail}"
                )
        self.stdout.write("")
        if report.applied:
            self.stdout.write(
                "Each RENAMED row wrote one DOCUMENT_TITLE_CHANGED event; no filename, "
                "byte, checksum or version was touched."
            )
        else:
            self.stdout.write(
                f"To apply exactly this file: --apply --expect-plan-sha256 {report.plan_sha256}"
            )
