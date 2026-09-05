"""Measure the assisted-intake rules against what people actually filed.

A read-only developer tool. For every Matter that already has extracted
documents *and* human-entered values, run the analyser and count how often
its strongest suggestion agrees with the person: senders, the response
deadline, the Menetlusliik, the Valdkonnad. The numbers say which rules are
worth their weight and which need work, which is what a rule vocabulary
needs in order to improve on evidence rather than on anecdote.

What it never does: write a row, dump a document, print an address. Output
is aggregate counts, and it refuses a real-data environment unless told, in
so many words, that the operator means it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from app.core.authorization import DEPARTMENT_VIEWER
from app.documents.enums import ExtractionState
from app.matters.intake_suggestions import SuggestedField, analyse_matter
from app.matters.intake_suggestions.vocabulary import RULES_VERSION
from app.matters.models import Matter


@dataclass
class Tally:
    """One field's scorecard."""

    #: Matters where the person recorded a value for this field.
    with_truth: int = 0
    #: …and the analyser offered at least one candidate.
    suggested: int = 0
    #: …and the strongest candidate was HIGH.
    high: int = 0
    #: …and the analyser declared a conflict.
    conflicts: int = 0
    #: …and the top candidate equals the recorded value.
    top1_agrees: int = 0
    #: …and any offered candidate equals the recorded value.
    topk_agrees: int = 0
    #: …and nothing was offered at all.
    none: int = 0

    def row(self, label: str) -> str:
        return (
            f"{label:<18} truth={self.with_truth:>4}  suggested={self.suggested:>4}  "
            f"high={self.high:>4}  conflicts={self.conflicts:>3}  top1={self.top1_agrees:>4}  "
            f"topk={self.topk_agrees:>4}  none={self.none:>4}"
        )


@dataclass
class Report:
    scanned: int = 0
    eligible: int = 0
    tallies: dict[str, Tally] = field(default_factory=dict)

    def tally(self, name: str) -> Tally:
        return self.tallies.setdefault(name, Tally())


class Command(BaseCommand):
    help = "Compare assisted-intake suggestions with human-entered Matter values. Read-only."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--limit", type=int, default=100, help="How many Matters to score.")
        parser.add_argument(
            "--real-data",
            action="store_true",
            help="Required in a REAL_DATA_ALLOWED environment. Still aggregate-only.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if settings.REAL_DATA_ALLOWED and not options["real_data"]:
            raise CommandError(
                "REAL_DATA_ALLOWED is set; pass --real-data to score real Matters "
                "(aggregate counts only, nothing is written)."
            )
        report = Report()
        # The shared-gate viewer: every NORMAL Matter and document, no
        # restricted content. Enough to measure rules; never a bypass.
        viewer = DEPARTMENT_VIEWER
        queryset = (
            Matter.objects.visible_to(viewer)
            .full_records()
            .filter(documents__current_version__extraction_state=ExtractionState.DONE)
            .distinct()
            .order_by("-created_at")[: options["limit"]]
        )
        for matter in queryset.prefetch_related("source_organisations", "policy_areas"):
            report.scanned += 1
            analysis = analyse_matter(matter, viewer)
            if not analysis.has_text:
                continue
            report.eligible += 1
            self._score_senders(report, matter, analysis)
            self._score_scalar(
                report,
                "response_deadline",
                truth=matter.response_deadline.isoformat() if matter.response_deadline else "",
                suggestions=analysis.fields.get(SuggestedField.RESPONSE_DEADLINE),
            )
            self._score_scalar(
                report,
                "track",
                truth=matter.track or "",
                suggestions=analysis.fields.get(SuggestedField.TRACK),
            )
            self._score_set(
                report,
                "policy_areas",
                truth={str(area.pk) for area in matter.policy_areas.all()},
                suggestions=analysis.fields.get(SuggestedField.POLICY_AREAS),
            )
        self._print(report)

    def _score_senders(self, report: Report, matter: Matter, analysis: Any) -> None:
        self._score_set(
            report,
            "source_organisations",
            truth={str(pk) for pk in matter.source_organisation_ids},
            suggestions=analysis.fields.get(SuggestedField.SOURCE_ORGANISATIONS),
        )

    @staticmethod
    def _score_scalar(report: Report, name: str, *, truth: str, suggestions: Any) -> None:
        tally = report.tally(name)
        if not truth:
            return
        tally.with_truth += 1
        offered = list(suggestions.offered) if suggestions is not None else []
        if not offered:
            tally.none += 1
            return
        tally.suggested += 1
        if offered[0].is_high:
            tally.high += 1
        if suggestions.conflict:
            tally.conflicts += 1
        if offered[0].value == truth:
            tally.top1_agrees += 1
        if any(candidate.value == truth for candidate in offered):
            tally.topk_agrees += 1

    @staticmethod
    def _score_set(report: Report, name: str, *, truth: set[str], suggestions: Any) -> None:
        tally = report.tally(name)
        if not truth:
            return
        tally.with_truth += 1
        offered = list(suggestions.offered) if suggestions is not None else []
        if not offered:
            tally.none += 1
            return
        tally.suggested += 1
        if offered[0].is_high:
            tally.high += 1
        if suggestions.conflict:
            tally.conflicts += 1
        if offered[0].value in truth:
            tally.top1_agrees += 1
        if any(candidate.value in truth for candidate in offered):
            tally.topk_agrees += 1

    def _print(self, report: Report) -> None:
        self.stdout.write(f"Assisted intake — rules {RULES_VERSION}")
        self.stdout.write(
            f"Scanned {report.scanned} Matters, {report.eligible} with readable text."
        )
        for name in ("source_organisations", "response_deadline", "track", "policy_areas"):
            self.stdout.write(report.tally(name).row(name))
        self.stdout.write("Nothing was written.")
