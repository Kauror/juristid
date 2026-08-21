"""The one entry point: a metric key in, a graded result out.

``compute(key, context)`` is how every surface asks for a number — the tabs, the
CSV exports and the tests alike. Nothing calls a selector function directly from
a view, so there is exactly one place where a key is resolved to a population
and exactly one place a test has to look to check what a card is showing.

``COMPUTERS`` is asserted at import time to cover the catalogue exactly, in both
directions. A definition with no implementation would be a metric documented and
never shown; an implementation with no definition would be a number on a page
with no reviewed meaning. Both are worth failing to start over.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.reporting import metric_catalogue as keys
from app.reporting.context import ReportingContext
from app.reporting.metric_catalogue import CATALOGUE
from app.reporting.metric_types import MetricResult
from app.reporting.selectors import (
    activity,
    documents,
    historical,
    opinions,
    organisations,
    quality,
)
from app.reporting.selectors import matters as matter_selectors
from app.reporting.selectors import submissions as submission_selectors

Computer = Callable[[ReportingContext], MetricResult]

COMPUTERS: dict[str, Computer] = {
    # Teemad
    keys.MATTERS_TOTAL: matter_selectors.matters_total,
    keys.MATTERS_BY_REPORTING_YEAR: matter_selectors.matters_by_reporting_year,
    keys.ACTIVE_FULL_MATTERS: matter_selectors.active_full_matters,
    keys.MATTERS_BY_RECORD_MODE: matter_selectors.matters_by_record_mode,
    keys.MATTERS_BY_ORIGIN: matter_selectors.matters_by_origin,
    keys.MATTERS_BY_STAGE: matter_selectors.matters_by_stage,
    keys.MATTERS_BY_OWNER: matter_selectors.matters_by_owner,
    keys.MATTERS_BY_POLICY_AREA: matter_selectors.matters_by_policy_area,
    keys.MATTERS_UNCLASSIFIED_POLICY_AREA: matter_selectors.matters_unclassified_policy_area,
    keys.MATTERS_BY_TRACK: matter_selectors.matters_by_track,
    keys.MATTERS_BY_TAG: matter_selectors.matters_by_tag,
    keys.MATTERS_WITH_HISTORICAL_SOURCE: matter_selectors.matters_with_historical_source,
    keys.MATTERS_WITHOUT_HISTORICAL_SOURCE: matter_selectors.matters_without_historical_source,
    keys.ONENOTE_ONLY_MATTERS: matter_selectors.onenote_only_matters,
    keys.MATTERS_WITH_MULTIPLE_SOURCE_PAGES: (matter_selectors.matters_with_multiple_source_pages),
    keys.HISTORICAL_SOURCE_COVERAGE_CLASSES: (matter_selectors.historical_source_coverage_classes),
    # Arvamused
    keys.SUBMISSIONS_SENT: submission_selectors.submissions_sent,
    keys.SUBMISSIONS_SENT_BY_PERIOD: submission_selectors.submissions_sent_by_period,
    keys.SUBMISSIONS_BY_RECIPIENT: submission_selectors.submissions_by_recipient,
    keys.SUBMISSIONS_BY_KIND: submission_selectors.submissions_by_kind,
    keys.MATTERS_BY_SUBMISSION_COUNT: submission_selectors.matters_by_submission_count,
    keys.MATTERS_WITH_MULTIPLE_SUBMISSIONS: (
        submission_selectors.matters_with_multiple_submissions
    ),
    # Koja tegevus
    keys.NEW_NATIVE_FULL_MATTERS: activity.new_native_full_matters,
    keys.ACTIVE_WITHOUT_NEXT_ACTION: activity.active_without_next_action,
    keys.ACTIVE_WITHOUT_OWNER: activity.active_without_owner,
    keys.ACTIVE_WITHOUT_STAGE: activity.active_without_stage,
    keys.RESPONSE_DEADLINES_OPEN: activity.response_deadlines_open,
    keys.OVERDUE_DO_DEADLINE: activity.overdue_do_deadline,
    keys.WAIT_REVIEW_DUE: activity.wait_review_due,
    keys.MONITOR_REVIEW_DUE: activity.monitor_review_due,
    keys.NEXT_ACTION_BY_KIND: activity.next_action_by_kind,
    keys.ENTRY_COUNT: activity.entry_count,
    keys.ENTRY_COUNT_BY_KIND: activity.entry_count_by_kind,
    # Organisatsioonid
    keys.MATTERS_BY_SOURCE_ORGANISATION: organisations.matters_by_source_organisation,
    keys.MATTERS_BY_ADDRESSEE_ORGANISATION: organisations.matters_by_addressee_organisation,
    # Ajalooline materjal
    keys.LEGACY_SOURCE_PAGES: historical.legacy_source_pages,
    keys.LEGACY_SOURCE_PAGES_BY_SECTION: historical.legacy_source_pages_by_section,
    keys.LEGACY_SOURCE_PAGES_BY_YEAR: historical.legacy_source_pages_by_year,
    keys.LEGACY_SOURCE_PAGES_BY_ROLE: historical.legacy_source_pages_by_role,
    keys.HISTORICAL_RESOURCE_OCCURRENCES: historical.historical_resource_occurrences,
    keys.HISTORICAL_UNIQUE_BINARY_CONTENTS: historical.historical_unique_binary_contents,
    keys.HISTORICAL_RESOURCE_BYTES: historical.historical_resource_bytes,
    keys.HISTORICAL_RESOURCES_BY_TYPE: historical.historical_resources_by_type,
    keys.HISTORICAL_EMAIL_RESOURCES: historical.historical_email_resources,
    keys.HISTORICAL_SIGNED_CONTAINERS: historical.historical_signed_containers,
    keys.RESOURCES_PER_PAGE: historical.resources_per_page,
    keys.RESOURCES_PER_MATTER: historical.resources_per_matter,
    keys.SOURCE_PAGE_TEXT_LENGTH: historical.source_page_text_length,
    keys.MATERIALISATION_STATUS: historical.materialisation_status,
    keys.MATERIALISATION_FAILED: historical.materialisation_failed,
    keys.READING_ORDER_AMBIGUOUS: historical.reading_order_ambiguous,
    # Dokumendid
    keys.EXTRACTION_ELIGIBLE: documents.extraction_eligible,
    keys.EXTRACTION_SUCCESS: documents.extraction_success,
    keys.EXTRACTION_PENDING: documents.extraction_pending,
    keys.EXTRACTION_AWAITING_SCANNER: documents.extraction_awaiting_scanner,
    keys.EXTRACTION_FAILED: documents.extraction_failed,
    keys.EXTRACTION_NOT_APPLICABLE: documents.extraction_not_applicable,
    keys.SEARCHABLE_DOCUMENT_COVERAGE: documents.searchable_document_coverage,
    # Andmekvaliteet
    keys.OPINION_ARCHIVE_OCCURRENCES: opinions.opinion_archive_occurrences,
    keys.OPINION_ARCHIVE_DISTINCT_BINARIES: opinions.opinion_archive_distinct_binaries,
    keys.OPINION_ARCHIVE_MATTER_COVERAGE: opinions.opinion_archive_matter_coverage,
    keys.OPINION_ARCHIVE_UNRESOLVED: opinions.opinion_archive_unresolved,
    keys.HISTORICAL_SUBMISSION_COVERAGE: opinions.historical_submission_coverage,
    keys.SUBMISSION_RECIPIENT_COVERAGE: opinions.submission_recipient_coverage,
    keys.RECONCILIATION_PENDING: quality.reconciliation_pending,
    keys.RECONCILIATION_CONFLICT: quality.reconciliation_conflict,
    keys.RECONCILIATION_BY_CLASS: quality.reconciliation_by_class,
    keys.UNLINKED_SUBSTANTIVE_PAGES: quality.unlinked_substantive_pages,
    keys.DATA_QUALITY_ATTENTION: quality.data_quality_attention,
}

_missing = set(CATALOGUE) - set(COMPUTERS)
_extra = set(COMPUTERS) - set(CATALOGUE)
if _missing or _extra:  # pragma: no cover - import-time guard
    raise RuntimeError(
        "The metric catalogue and its implementations disagree. "
        f"Defined but not implemented: {sorted(_missing)}. "
        f"Implemented but not defined: {sorted(_extra)}."
    )


def compute(key: str, context: ReportingContext) -> MetricResult:
    """Answer one metric. The only way a surface obtains a number."""
    try:
        computer = COMPUTERS[key]
    except KeyError as exc:  # pragma: no cover - programming error
        raise KeyError(f"No implementation for metric {key!r}.") from exc
    return computer(context)


def compute_many(metric_keys: list[str], context: ReportingContext) -> list[MetricResult]:
    return [compute(key, context) for key in metric_keys]


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@dataclass
class Page:
    """One tab's worth of answers, already computed."""

    cards: list[MetricResult] = field(default_factory=list)
    charts: list[MetricResult] = field(default_factory=list)
    tables: list[MetricResult] = field(default_factory=list)

    @property
    def all_results(self) -> list[MetricResult]:
        return [*self.cards, *self.charts, *self.tables]


#: Üldpilt is five numbers and six charts, and no more. A landing page that
#: tries to say everything says nothing, and every card here links into the
#: tab that explains it (brief 16, 17).
OVERVIEW_CARDS = [
    keys.MATTERS_TOTAL,
    keys.ACTIVE_FULL_MATTERS,
    keys.SUBMISSIONS_SENT,
    keys.MATTERS_WITH_HISTORICAL_SOURCE,
    keys.DATA_QUALITY_ATTENTION,
]

OVERVIEW_CHARTS = [
    keys.MATTERS_BY_REPORTING_YEAR,
    keys.MATTERS_BY_ORIGIN,
    keys.MATTERS_BY_POLICY_AREA,
    keys.SUBMISSIONS_SENT_BY_PERIOD,
    keys.HISTORICAL_SOURCE_COVERAGE_CLASSES,
    keys.MATERIALISATION_STATUS,
]

MATTERS_CARDS = [
    keys.MATTERS_TOTAL,
    keys.ACTIVE_FULL_MATTERS,
    keys.ONENOTE_ONLY_MATTERS,
    keys.MATTERS_UNCLASSIFIED_POLICY_AREA,
]

MATTERS_CHARTS = [
    keys.MATTERS_BY_REPORTING_YEAR,
    keys.MATTERS_BY_RECORD_MODE,
    keys.MATTERS_BY_ORIGIN,
    keys.MATTERS_BY_STAGE,
    keys.MATTERS_BY_TRACK,
    keys.MATTERS_BY_POLICY_AREA,
    keys.MATTERS_BY_TAG,
    keys.MATTERS_BY_OWNER,
    keys.HISTORICAL_SOURCE_COVERAGE_CLASSES,
]

MATTERS_TABLES = [
    keys.MATTERS_BY_SOURCE_ORGANISATION,
    keys.MATTERS_BY_ADDRESSEE_ORGANISATION,
]

ACTIVITY_CARDS = [
    keys.NEW_NATIVE_FULL_MATTERS,
    keys.SUBMISSIONS_SENT,
    keys.ENTRY_COUNT,
    keys.ACTIVE_WITHOUT_NEXT_ACTION,
    keys.OVERDUE_DO_DEADLINE,
]

ACTIVITY_CHARTS = [
    keys.SUBMISSIONS_SENT_BY_PERIOD,
    keys.SUBMISSIONS_BY_KIND,
    keys.MATTERS_BY_SUBMISSION_COUNT,
    keys.NEXT_ACTION_BY_KIND,
    keys.ENTRY_COUNT_BY_KIND,
]

ACTIVITY_TABLES = [
    keys.SUBMISSIONS_BY_RECIPIENT,
]

HISTORICAL_CARDS = [
    keys.LEGACY_SOURCE_PAGES,
    keys.HISTORICAL_RESOURCE_OCCURRENCES,
    keys.HISTORICAL_UNIQUE_BINARY_CONTENTS,
    keys.HISTORICAL_RESOURCE_BYTES,
    keys.HISTORICAL_SIGNED_CONTAINERS,
    keys.OPINION_ARCHIVE_OCCURRENCES,
    keys.OPINION_ARCHIVE_DISTINCT_BINARIES,
]

HISTORICAL_CHARTS = [
    keys.HISTORICAL_RESOURCES_BY_TYPE,
    keys.MATERIALISATION_STATUS,
    keys.LEGACY_SOURCE_PAGES_BY_SECTION,
    keys.LEGACY_SOURCE_PAGES_BY_YEAR,
    keys.LEGACY_SOURCE_PAGES_BY_ROLE,
]

HISTORICAL_TABLES = [
    keys.RESOURCES_PER_PAGE,
    keys.RESOURCES_PER_MATTER,
    keys.SOURCE_PAGE_TEXT_LENGTH,
]

QUALITY_CARDS = [
    keys.DATA_QUALITY_ATTENTION,
    keys.RECONCILIATION_PENDING,
    keys.RECONCILIATION_CONFLICT,
    keys.SEARCHABLE_DOCUMENT_COVERAGE,
    # The opinion archive's own coverage. Grouped here rather than under
    # "Koja tegevus" because an unmatched letter is a reconciliation gap, not
    # a statement about how much advocacy happened (Stage-2H brief 55).
    keys.OPINION_ARCHIVE_MATTER_COVERAGE,
    keys.HISTORICAL_SUBMISSION_COVERAGE,
    keys.SUBMISSION_RECIPIENT_COVERAGE,
    keys.OPINION_ARCHIVE_UNRESOLVED,
]

QUALITY_CHARTS = [
    keys.RECONCILIATION_BY_CLASS,
]


def overview_page(context: ReportingContext) -> Page:
    return Page(
        cards=compute_many(OVERVIEW_CARDS, context),
        charts=compute_many(OVERVIEW_CHARTS, context),
    )


def matters_page(context: ReportingContext) -> Page:
    return Page(
        cards=compute_many(MATTERS_CARDS, context),
        charts=compute_many(MATTERS_CHARTS, context),
        tables=compute_many(MATTERS_TABLES, context),
    )


def activity_page(context: ReportingContext) -> Page:
    return Page(
        cards=compute_many(ACTIVITY_CARDS, context),
        charts=compute_many(ACTIVITY_CHARTS, context),
        tables=compute_many(ACTIVITY_TABLES, context),
    )


def historical_page(context: ReportingContext) -> Page:
    return Page(
        cards=compute_many(HISTORICAL_CARDS, context),
        charts=compute_many(HISTORICAL_CHARTS, context),
        tables=compute_many(HISTORICAL_TABLES, context),
    )


def quality_page(context: ReportingContext) -> Page:
    return Page(
        cards=compute_many(QUALITY_CARDS, context),
        charts=compute_many(QUALITY_CHARTS, context),
        tables=[
            compute(keys.EXTRACTION_SUCCESS, context),
            compute(keys.EXTRACTION_PENDING, context),
            compute(keys.EXTRACTION_AWAITING_SCANNER, context),
            compute(keys.EXTRACTION_FAILED, context),
            compute(keys.EXTRACTION_NOT_APPLICABLE, context),
        ],
    )
