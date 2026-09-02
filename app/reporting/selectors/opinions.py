"""Arvamuste arhiiv — how much of the historical corpus actually reached a Matter.

These are coverage metrics, and the wording matters more than the arithmetic.
An archive file with no Matter is not a missing opinion; it is evidence Koda
holds and has not yet placed. A Matter with no submission is not a matter Koda
stayed silent on. Every label here says which of those it means, because the
alternative is a dashboard that quietly converts an unresolved queue into a
claim about the department's output (Stage-2H brief 55, 81).

Authorization follows the same rule as every other reporting selector: anything
that names a Matter is scoped through it. An archive item that has not been
linked to anything names no Matter and carries no Matter content — only a
filename, a size and a hash — so it is counted for everyone. The moment a
candidate names a Matter, the reader has to be allowed to see that Matter
(brief 71).
"""

from __future__ import annotations

from django.db.models import Q, QuerySet
from django.urls import reverse

from app.core.authorization import apply as apply_scope
from app.core.authorization import child_visibility_q, matter_visibility_q, scope_for_user
from app.legacy_import.opinion_access import may_use_opinion_queue
from app.legacy_import.opinion_archive import (
    OpinionArchiveItem,
    OpinionMatchCandidate,
    OpinionSubmissionImport,
)
from app.legacy_import.opinion_enums import OpinionCandidateState
from app.reporting import metric_catalogue as keys
from app.reporting.context import ReportingContext
from app.reporting.metric_catalogue import definition
from app.reporting.metric_types import MetricResult
from app.reporting.selectors.base import count, simple_result
from app.submissions.enums import RecipientRole, SubmissionStatus
from app.submissions.models import Submission


def visible_candidates(context: ReportingContext) -> QuerySet[OpinionMatchCandidate]:
    scope = scope_for_user(context.viewer)
    with_matter = apply_scope(
        OpinionMatchCandidate.objects.filter(matter__isnull=False),
        matter_visibility_q(scope, prefix="matter__"),
    )
    return OpinionMatchCandidate.objects.filter(
        Q(matter__isnull=True) | Q(pk__in=with_matter.values("pk"))
    )


def _queue_url(context: ReportingContext, **params: str) -> str:
    # The link this builds *is* `legacy_import:opinion_queue`, so offer and
    # serve are decided by one predicate. Offering a button that can only
    # produce a 403 is the failure `archive_views` names at its own call to
    # this (DUP-05).
    if not may_use_opinion_queue(context.viewer):
        return ""
    base = reverse("legacy_import:opinion_queue")
    query = "&".join(f"{key}={value}" for key, value in params.items() if value)
    return f"{base}?{query}" if query else base


def _sent_submissions(context: ReportingContext) -> QuerySet[Submission]:
    scope = scope_for_user(context.viewer)
    return apply_scope(
        Submission.objects.filter(status=SubmissionStatus.SENT),
        child_visibility_q(scope),
    )


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


def opinion_archive_occurrences(context: ReportingContext) -> MetricResult:
    spec = definition(keys.OPINION_ARCHIVE_OCCURRENCES)
    total = OpinionArchiveItem.objects.count()
    return simple_result(
        spec,
        context=context,
        value=total,
        url=_queue_url(context, olek=""),
    )


def opinion_archive_distinct_binaries(context: ReportingContext) -> MetricResult:
    """Distinct bytes, which is a different number from occurrences on purpose.

    Reporting only one of the two would answer a question nobody asked: how
    many letters exist is the binary count, how many times they were filed is
    the occurrence count (brief 29).
    """
    spec = definition(keys.OPINION_ARCHIVE_DISTINCT_BINARIES)
    return simple_result(
        spec,
        context=context,
        value=OpinionArchiveItem.objects.values("sha256").distinct().count(),
        population_count=OpinionArchiveItem.objects.count(),
    )


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def opinion_archive_matter_coverage(context: ReportingContext) -> MetricResult:
    """Share of archive files that reached a Matter, resolved or reviewed."""
    spec = definition(keys.OPINION_ARCHIVE_MATTER_COVERAGE)
    total = OpinionArchiveItem.objects.count()
    settled = count(
        OpinionArchiveItem.objects.filter(
            candidates__matter__isnull=False,
            candidates__state__in=(
                OpinionCandidateState.APPLIED,
                OpinionCandidateState.LINKED,
            ),
        )
    )
    percent = round(settled / total * 100) if total else 0
    return simple_result(
        spec,
        context=context,
        value=percent,
        population_count=total,
        coverage_count=settled,
        coverage_denominator=total,
        url=_queue_url(context, olek=OpinionCandidateState.PENDING.value),
    )


def opinion_archive_unresolved(context: ReportingContext) -> MetricResult:
    spec = definition(keys.OPINION_ARCHIVE_UNRESOLVED)
    pending = visible_candidates(context).filter(state=OpinionCandidateState.PENDING)
    return simple_result(
        spec,
        context=context,
        value=count(pending),
        population_count=count(visible_candidates(context)),
        url=_queue_url(context, olek=OpinionCandidateState.PENDING.value),
    )


def historical_submission_coverage(context: ReportingContext) -> MetricResult:
    spec = definition(keys.HISTORICAL_SUBMISSION_COVERAGE)
    total = OpinionArchiveItem.objects.values("sha256").distinct().count()
    reconstructed = OpinionSubmissionImport.objects.values("item__sha256").distinct().count()
    percent = round(reconstructed / total * 100) if total else 0
    return simple_result(
        spec,
        context=context,
        value=percent,
        population_count=total,
        coverage_count=reconstructed,
        coverage_denominator=total,
    )


def submission_recipient_coverage(context: ReportingContext) -> MetricResult:
    """Sent submissions carrying at least one resolved addressee.

    "Teadmiseks" recipients are excluded, because the question this answers is
    who Koda formally wrote to. A submission whose recipient string never
    resolved to an Organisation counts as uncovered rather than as having no
    recipient (brief 45, 56).
    """
    spec = definition(keys.SUBMISSION_RECIPIENT_COVERAGE)
    sent = _sent_submissions(context)
    total = count(sent)
    with_addressee = count(sent.filter(recipient_rows__role=RecipientRole.ADDRESSEE))
    percent = round(with_addressee / total * 100) if total else 0
    return simple_result(
        spec,
        context=context,
        value=percent,
        population_count=total,
        coverage_count=with_addressee,
        coverage_denominator=total,
    )
