"""Plan — and, behind four digests, apply — a newer register snapshot.

    python manage.py refresh_current_register plan \\
        --workbook "Tööd eelnõudega28.08.xlsx" \\
        --campaigns campaigns-2026-08-28.csv \\
        --rows /local/refresh-rows.json

    python manage.py refresh_current_register apply \\
        --workbook "Tööd eelnõudega28.08.xlsx" \\
        --expect-plan-sha256 <digest> \\
        --links /local/approved-links.json \\
        --expect-mapping-sha256 <digest>

``plan`` reads the database, decides everything and writes nothing — no Matter,
no action, no engagement, no audit row. It prints the plan digest, which is the
only thing ``apply`` will accept.

Four digests, and each answers a different question
---------------------------------------------------
The **workbook** digest is computed here from the file's own bytes and checked
against the reviewed list, so the operation cannot run against a workbook nobody
approved or against a file that has been edited since somebody looked at it.

The **plan** digest says nothing in the database moved between deciding and
writing. Between the two runs a lawyer may have set a next action by hand or
closed a file; a digest that no longer matches means the plan somebody approved
is not the plan in front of the command, and the answer is to look again rather
than to write most of it.

The **campaign** digest names the export the outreach candidates came from.

The **mapping** digest names the links a person actually approved. Candidates
are never links: without ``--links`` this command reports campaign candidates
and writes none of them.

What stays out of the repository
--------------------------------
Everything the command reads and most of what it writes. The workbook holds
operational case data and the campaign export holds member mailing data; both
are read from wherever the operator keeps them and neither is copied anywhere.
``--rows`` writes a per-Matter review file to a local path: it carries stable
identifiers, the reading and the campaign's own published title, and it carries
no register sentence — those appear only as hashes.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.legacy_import.final_cutover import ACTIONS, reviewed_snapshot
from app.legacy_import.next_action_enrichment import OUTCOMES
from app.legacy_import.register_outreach import (
    CAMPAIGN_COLUMNS,
    Campaign,
    MappingError,
    candidate_rows,
    mapping_digest,
    read_campaigns,
    read_mapping,
)
from app.legacy_import.register_refresh import (
    PlanChanged,
    UnreviewedSnapshot,
    apply_refresh_plan,
    build_refresh_plan,
    protected_rows,
    summary,
)

#: The pilot window for campaign candidates. Deliberately a constant rather than
#: an option: "which months of outreach are we placing" is a reviewed decision
#: about a pilot, and a command-line flag would let a later run silently widen
#: it to a decade of mailings nobody has looked at (brief 21).
CAMPAIGN_WINDOW: tuple[dt.date, dt.date] = (dt.date(2026, 1, 1), dt.date(2026, 8, 28))

_ACTION_LABELS: dict[str, str] = {
    "ACTIVATE": "becomes current work",
    "KEEP_CURRENT": "stays current work",
    "RETIRE": "leaves current work",
    "ALREADY_RETIRED": "already not current",
    "NATIVE_SKIP": "created here; the register does not apply",
    "REVIEW_REQUIRED": "a person decides",
}

_FIELD_LABELS: dict[str, str] = {
    "owner": "VASTUTAJA moves the owner",
    "stage": "HETKESEIS moves the stage",
    "received_date": "SISSE moves the received date",
    "response_deadline": "ARVAMUSE TÄHTAEG moves the deadline",
    "addressee_organisation": "KELLELE moves the addressee",
}

_SENT_LABELS: dict[str, str] = {
    "date": "VÄLJA is a date",
    "not_sent": "VÄLJA says the opinion was not sent",
    "recorded_other": "VÄLJA holds something else",
    "blank": "VÄLJA is empty",
}

_FEEDBACK_LABELS: dict[str, str] = {
    "populated": "a number is recorded",
    "explicit_zero": "an explicit zero",
    "blank": "not recorded (not a zero)",
}


def file_digest(path: Path) -> str:
    """SHA-256 of a file, read in blocks so a large workbook is not held whole."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class Command(BaseCommand):
    help = (
        "Plan and apply a newer reviewed register snapshot: currency, "
        "source-authoritative fields, JÄRGMISEKS and reviewed outreach links."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("mode", choices=["plan", "apply"])
        parser.add_argument(
            "--workbook",
            required=True,
            help="Path to the maintained workbook. Hashed here; never copied.",
        )
        parser.add_argument(
            "--campaigns",
            default="",
            help=(
                "Path to the Sendsmaily campaign export. Only the five recorded "
                "columns are read; no open, click or bounce figure is imported."
            ),
        )
        parser.add_argument(
            "--links",
            default="",
            help=(
                "Path to the reviewed outreach mapping. The only input that can "
                "create a Kaasamine record."
            ),
        )
        parser.add_argument("--expect-plan-sha256", default="")
        parser.add_argument("--expect-mapping-sha256", default="")
        parser.add_argument(
            "--today",
            default="",
            help="Evaluate staleness against this date instead of today (ISO).",
        )
        parser.add_argument("--json", action="store_true")
        parser.add_argument(
            "--rows",
            default="",
            help="Write the per-Matter operator review file to this local path.",
        )
        parser.add_argument(
            "--candidates",
            default="",
            help="Write the outreach candidate review file to this local path.",
        )

    # -- inputs ------------------------------------------------------------

    def _workbook_digest(self, options: dict[str, Any]) -> str:
        path = Path(str(options["workbook"]))
        if not path.is_file():
            raise CommandError(f"No workbook at {path}.")
        digest = file_digest(path)
        snapshot = reviewed_snapshot(digest)
        if snapshot is None:
            raise CommandError(
                f"{path.name} hashes to {digest[:16]}…, which is not a reviewed "
                "snapshot. Applying a workbook nobody approved is the one thing "
                "this operation must not do; record the digest in "
                "REVIEWED_SNAPSHOTS first."
            )
        self.stdout.write(f"Workbook  {snapshot.label}")
        self.stdout.write(f"          {digest}")
        return digest

    def _campaigns(self, options: dict[str, Any]) -> tuple[list[Campaign], dict[str, int]]:
        if not options["campaigns"]:
            return [], {}
        path = Path(str(options["campaigns"]))
        if not path.is_file():
            raise CommandError(f"No campaign export at {path}.")
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            missing = [name for name in CAMPAIGN_COLUMNS if name not in (reader.fieldnames or [])]
            if missing:
                raise CommandError(f"The campaign export is missing columns: {missing}.")
            since, until = CAMPAIGN_WINDOW
            campaigns, tally = read_campaigns(reader, since=since, until=until)
        self.stdout.write(f"Campaigns {path.name} — {file_digest(path)}")
        return campaigns, tally

    def _links(self, options: dict[str, Any]) -> Any:
        if not options["links"]:
            return ()
        path = Path(str(options["links"]))
        if not path.is_file():
            raise CommandError(f"No reviewed mapping at {path}.")
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
            links = read_mapping(entries)
        except (MappingError, ValueError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(f"Mapping   {len(links)} approved links — {mapping_digest(links)}")
        return links

    # -- the run -----------------------------------------------------------

    def handle(self, *args: Any, **options: Any) -> None:
        digest = self._workbook_digest(options)
        campaigns, tally = self._campaigns(options)
        links = self._links(options)

        today = None
        if options["today"]:
            try:
                today = dt.date.fromisoformat(str(options["today"]))
            except ValueError as error:
                raise CommandError(f"Unreadable --today {options['today']!r}.") from error

        plan = build_refresh_plan(
            snapshot_sha256=digest,
            today=today,
            campaigns=campaigns if campaigns else None,
            campaign_window=CAMPAIGN_WINDOW if campaigns else None,
        )
        if plan.outreach is not None:
            plan.outreach.read_tally = tally

        figures = summary(plan)
        if options["json"]:
            self.stdout.write(json.dumps(figures, indent=2, ensure_ascii=False))
        else:
            self._report(figures)

        if options["rows"]:
            self._write(Path(str(options["rows"])), protected_rows(plan), "per-Matter review")
        if options["candidates"] and plan.outreach is not None:
            self._write(
                Path(str(options["candidates"])),
                candidate_rows(plan.outreach),
                "outreach candidate",
            )

        if options["mode"] == "plan":
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS(f"Plan digest {plan.digest}"))
            self.stdout.write(
                "Nothing was written: no Matter changed state, no next action was "
                "created or withdrawn, no engagement was recorded and no register "
                "cell was rewritten."
            )
            return

        try:
            result = apply_refresh_plan(
                plan,
                expect_plan_sha256=str(options["expect_plan_sha256"]),
                links=links,
                expect_mapping_sha256=str(options["expect_mapping_sha256"]),
            )
        except (PlanChanged, UnreviewedSnapshot, MappingError) as error:
            raise CommandError(str(error)) from error

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Applied"))
        for label, value in (
            ("became current", result.activated),
            ("stayed current", result.kept),
            ("left current work", result.retired),
            ("fields refreshed", result.refreshed),
            ("derived state rows", result.state_rows),
            ("next actions created", result.actions_created),
            ("next actions refreshed", result.actions_refreshed),
            ("next actions withdrawn", result.actions_withdrawn),
            ("engagements created", result.engagements_created),
            ("engagements corrected", result.engagements_updated),
        ):
            self.stdout.write(f"  {label:<24} {value}")

    # -- output ------------------------------------------------------------

    def _write(self, path: Path, rows: list[dict[str, Any]], what: str) -> None:
        path.write_text(
            json.dumps(rows, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        self.stdout.write(f"Wrote {len(rows)} {what} rows to {path}")

    def _report(self, figures: dict[str, Any]) -> None:
        write = self.stdout.write
        write(self.style.MIGRATE_HEADING("Current register refresh"))
        write(f"  refresh version     {figures['refresh_version']}")
        write(
            f"  snapshot            {figures['snapshot_label'] or figures['snapshot_sha256'][:16]}"
        )
        write(f"  snapshot date       {figures['snapshot_date']}")
        write(f"  reviewed            {'yes' if figures['reviewed_snapshot'] else 'NO'}")
        write(f"  current scope       {figures['current_scope_years']}")
        write(f"  evaluated on        {figures['evaluated_on']}")
        write(f"  examined rows       {figures['examined_rows']}")
        for sheet, count in figures["examined_by_sheet"].items():
            write(f"    sheet {sheet:<12} {count}")
        write("")

        write("Current work")
        write(f"  before              {figures['current_before']}")
        write(f"  after               {figures['current_after']}")
        for name in ACTIONS:
            write(f"  {_ACTION_LABELS[name]:<44} {figures['actions'][name]}")
        for reason, count in figures["review_reasons"].items():
            write(f"    review: {reason:<36} {count}")
        write("")

        write("Source-authoritative fields")
        for name, label in _FIELD_LABELS.items():
            write(f"  {label:<44} {figures['field_changes'][name]}")
        write(
            f"  {'multi-addressee rows (canonical untouched)':<44} "
            f"{figures['multi_addressee_rows']}"
        )
        for item in figures["unresolved_owners"]:
            write(f"    unresolved owner: {item['value']:<26} {item['rows']}")
        for item in figures["unresolved_organisations"]:
            write(f"    unresolved organisation: {item['value'][:40]:<40} {item['rows']}")
        write("")

        write("VÄLJA")
        for key, label in _SENT_LABELS.items():
            write(f"  {label:<44} {figures['opinion_sent'][key]}")
        write(
            f"  {'Submissions created from VÄLJA':<44} {figures['submissions_created_from_valja']}"
        )
        write("")

        write("Member feedback (register observation, not campaign recipients)")
        for column in ("member_feedback_requested", "member_feedback_responded"):
            write(f"  {column}")
            for key, label in _FEEDBACK_LABELS.items():
                write(f"    {label:<42} {figures[column][key]}")
        write("")

        actions = figures["next_actions"]
        write(f"JÄRGMISEKS (parser {actions['parser_version']})")
        for name in OUTCOMES:
            write(f"  {name:<44} {actions['outcomes'][name]}")
        for reason, count in actions["review_reasons"].items():
            write(f"    {reason:<42} {count}")
        write("")

        if "outreach" in figures:
            reach = figures["outreach"]
            write(f"Outreach candidates (matcher {reach['matcher_version']})")
            write(f"  window              {reach['window'][0]} … {reach['window'][1]}")
            write(f"  campaigns in window {reach['campaigns_in_window']}")
            for name, count in reach["candidates"].items():
                write(f"  {name:<44} {count}")
            write(f"  {'campaigns with no candidate':<44} {reach['campaigns_unmatched']}")
            write(
                f"  {'written without a reviewed mapping':<44} "
                f"{reach['writes_without_reviewed_mapping']}"
            )
            write("")
            write(f"  campaign set (pinned in the plan digest) {reach['campaign_set_sha256']}")
            if reach["campaign_file_sha256"]:
                write(f"  campaign file (evidence only)            {reach['campaign_file_sha256']}")
