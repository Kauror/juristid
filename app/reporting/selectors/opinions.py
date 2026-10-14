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
from app.matters.enums import MatterDataClass
from app.reporting import metric_catalogue as keys
from app.reporting.context import ReportingContext
from app.reporting.metric_catalogue import definition
from app.reporting.metric_types import MetricResult
from app.reporting.selectors.base import count, reporting_population, simple_result
from app.submissions.enums import RecipientRole, SubmissionStatus
from app.submissions.models import Submission


def visible_candidates(context: ReportingContext) -> QuerySet[OpinionMatchCandidate]:
    """Archive match candidates a published statistic may count.

    A candidate naming no Matter carries no Matter content and is counted for
    everyone. One naming a Matter counts only when the reader may see that
    Matter **and the Matter is real data**: a file settled on a TEST Matter is
    a test of the archive flow, not coverage of Koda's archive, and moved the
    published percentage (QA-STATS-04, docs/adr/0024 — reporting opts in to
    real data, it is never the default).
    """
    scope = scope_for_user(context.viewer)
    with_matter = apply_scope(
        OpinionMatchCandidate.objects.filter(
            matter__isnull=False, matter__data_class=MatterDataClass.REAL
        ),
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
    """Sent opinions this viewer may read, on real-data Matters only.

    An opinion sent on a TEST Matter is a test of the sending form, not a
    letter Koda wrote, and every other submission figure already leaves it out
    by starting at `visible_matters`. This one is not narrowed by the filter
    bar, so it starts at the population beneath it, `reporting_population`.
    """
    scope = scope_for_user(context.viewer)
    return apply_scope(
        Submission.objects.filter(
            status=SubmissionStatus.SENT,
            matter__in=reporting_population(context),
        ),
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


#: Said beside both coverage figures below, as `archive.py` says it beside its own.
VISIBLE_MATTERS_ONLY_NOTE = "Loetakse ainult seoseid teemadega, mida sina näed."


def opinion_archive_matter_coverage(context: ReportingContext) -> MetricResult:
    """Share of archive files that reached a Matter, resolved or reviewed.

    **The numerator is the reader's, the denominator the archive's** (AUD-044).
    A file that reached a Matter names that Matter, so it counts only when the
    reader may see the Matter — through `visible_candidates`, as the queue
    figure beside it already did; a file linked to a restricted Matter would
    otherwise move this percentage for somebody who may not know the Matter
    exists. The inventory itself names no Matter and stays the whole archive,
    the rule `archive.py`'s link coverage follows.
    """
    spec = definition(keys.OPINION_ARCHIVE_MATTER_COVERAGE)
    total = OpinionArchiveItem.objects.count()
    settled_candidates = visible_candidates(context).filter(
        matter__isnull=False,
        state__in=(OpinionCandidateState.APPLIED, OpinionCandidateState.LINKED),
    )
    settled = count(OpinionArchiveItem.objects.filter(pk__in=settled_candidates.values("item_id")))
    percent = round(settled / total * 100) if total else 0
    return simple_result(
        spec,
        context=context,
        value=percent,
        population_count=total,
        coverage_count=settled,
        coverage_denominator=total,
        url=_queue_url(context, olek=OpinionCandidateState.PENDING.value),
        notes=(VISIBLE_MATTERS_ONLY_NOTE,),
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
    """Archive files reconstructed into a submission, as distinct bytes.

    Scoped like the figure above (AUD-044): a reconstruction is a submission on
    a Matter, so it counts only where the reader may see that submission
    (`child_visibility_q`, the rule `_sent_submissions` follows) — and only on a
    real-data Matter. A reconstruction onto a TEST Matter is not coverage of
    Koda's archive; counting it moved this from 0 % to 10 % (QA-STATS-04). The
    denominator is the archive inventory, which names no Matter.
    """
    spec = definition(keys.HISTORICAL_SUBMISSION_COVERAGE)
    total = OpinionArchiveItem.objects.values("sha256").distinct().count()
    visible_submissions = apply_scope(
        Submission.objects.filter(matter__data_class=MatterDataClass.REAL),
        child_visibility_q(scope_for_user(context.viewer)),
    )
    reconstructed = (
        OpinionSubmissionImport.objects.filter(submission__in=visible_submissions.values("pk"))
        .values("item__sha256")
        .distinct()
        .count()
    )
    percent = round(reconstructed / total * 100) if total else 0
    return simple_result(
        spec,
        context=context,
        value=percent,
        population_count=total,
        coverage_count=reconstructed,
        coverage_denominator=total,
        notes=(VISIBLE_MATTERS_ONLY_NOTE,),
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
