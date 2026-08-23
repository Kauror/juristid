"""Plan — and, behind two digests, apply — structured actions from ``JÄRGMISEKS``.

    python manage.py register_next_action_enrichment plan \\
        --expect-snapshot-sha256 <sha>

    python manage.py register_next_action_enrichment apply \\
        --expect-snapshot-sha256 <sha> --expect-plan-sha256 <digest>

``plan`` reads the database, decides everything and writes nothing — no Matter,
no action, no audit row. It prints the plan digest, which is the only thing
``apply`` will accept.

Neither mode is the default, and ``apply`` needs a digest a person has read
rather than a flag they can type from memory. Between the two runs anything may
have moved: a lawyer may have set a next action by hand, the register cutover
may have rebuilt the derived state. A digest that no longer matches means the
plan somebody approved is not the plan in front of the command, and the answer
is to look again rather than to write most of it.

The output is aggregate. Matter titles and the register's own sentences are
source content and do not appear; ``--rows`` writes a per-proposal file for
operator review that carries stable identifiers and a hash of each sentence,
never the sentence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.legacy_import.next_action_enrichment import (
    OUTCOMES,
    MixedSnapshot,
    PlanChanged,
    UnknownSnapshot,
    apply_plan,
    build_plan,
    protected_rows,
    summary,
)

_OUTCOME_LABELS: dict[str, str] = {
    "AUTO": "would become a next action",
    "REVIEW_REQUIRED": "review required",
    "STALE_SOURCE": "understood, but its period has passed",
    "SKIP_EXISTING_ACTION_HISTORY": "a person already worked this file",
    "SKIP_NOT_CURRENT": "not current in the register",
    "SKIP_CLOSED": "Matter is closed",
    "SKIP_ARCHIVE_RECORD": "archive record, not current work",
    "SKIP_TEST_DATA": "development record",
    "SKIP_EMPTY": "no instruction written",
}

_REASON_LABELS: dict[str, str] = {
    "NO_KIND": "no reviewed verb; the instruction's kind is unstated",
    "AMBIGUOUS_KIND": "two kinds named in one sentence",
    "AMBIGUOUS_DATE": "two plausible dates",
    "UNREADABLE_DATE": "a date or period that cannot exist",
    "DO_WITHOUT_DATE": "work to do, with no date and no honest date meaning",
    "DO_DATE_WITHOUT_DEADLINE_WORDING": "a day beside a verb, not stated to be a deadline",
    "APPROXIMATE_DEADLINE": "deadline wording attached to a period rather than a day",
}


class Command(BaseCommand):
    help = (
        "Plan structured next actions from the approved register's JÄRGMISEKS "
        "column, and apply only the deterministic ones."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("mode", choices=["plan", "apply"])
        parser.add_argument(
            "--expect-snapshot-sha256",
            required=True,
            help="The approved workbook digest the derived register state must carry.",
        )
        parser.add_argument(
            "--expect-plan-sha256",
            default="",
            help="Required by apply: the plan digest a person reviewed.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Print the aggregate report as JSON instead of text.",
        )
        parser.add_argument(
            "--rows",
            default="",
            help=(
                "Write a per-proposal review file to this path. Stays local: it "
                "names Matters, and source sentences appear only as hashes."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            plan = build_plan(snapshot_sha256=str(options["expect_snapshot_sha256"]))
        except (UnknownSnapshot, MixedSnapshot) as error:
            raise CommandError(str(error)) from error

        figures = summary(plan)
        if options["json"]:
            self.stdout.write(json.dumps(figures, indent=2, ensure_ascii=False))
        else:
            self._report(figures)

        if options["rows"]:
            path = Path(options["rows"])
            path.write_text(
                json.dumps(protected_rows(plan), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            self.stdout.write("")
            self.stdout.write(f"Review file written to {path} — keep it local.")

        if options["mode"] == "plan":
            self.stdout.write("")
            self.stdout.write("Plan only: nothing was written to the database.")
            self.stdout.write(f"  plan digest  {figures['plan_sha256']}")
            return

        expected = str(options["expect_plan_sha256"]).strip()
        if not expected:
            raise CommandError("apply needs --expect-plan-sha256 from a reviewed plan run.")

        try:
            result = apply_plan(plan, expect_plan_sha256=expected)
        except PlanChanged as error:
            raise CommandError(str(error)) from error

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Applied"))
        self.stdout.write(f"  next actions created  {result.created}")
        self.stdout.write(f"  parser version        {result.parser_version}")
        self.stdout.write(
            "  no existing action was superseded, no register cell was rewritten, "
            "and no Matter changed state."
        )

    # -- output ------------------------------------------------------------

    def _report(self, figures: dict[str, Any]) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("JÄRGMISEKS enrichment"))
        self.stdout.write(f"  parser version      {figures['parser_version']}")
        self.stdout.write(f"  snapshot            {figures['snapshot_sha256'][:16]}…")
        self.stdout.write(f"  evaluated on        {figures['evaluated_on']}")
        self.stdout.write(f"  register rows       {figures['register_rows']}")
        self.stdout.write(f"  source instructions {figures['source_instructions']}")
        self.stdout.write("")

        self.stdout.write("Outcome")
        for name in OUTCOMES:
            self.stdout.write(f"  {_OUTCOME_LABELS[name]:<48} {figures['outcomes'][name]}")

        if figures["review_reasons"]:
            self.stdout.write("")
            self.stdout.write("Why review is required")
            for reason, count in figures["review_reasons"].items():
                self.stdout.write(f"  {_REASON_LABELS.get(reason, reason):<48} {count}")

        self.stdout.write("")
        self.stdout.write("What the AUTO set would create")
        for label, key in (
            ("kind", "kinds"),
            ("date meaning", "date_semantics"),
            ("date precision", "date_precisions"),
        ):
            for value, count in figures[key].items():
                self.stdout.write(f"  {label:<16} {value:<20} {count}")
        self.stdout.write(f"  {'no date':<16} {'':<20} {figures['auto_without_date']}")
        self.stdout.write(
            f"  {'no responsible':<16} {'':<20} {figures['auto_without_responsible']}"
        )
