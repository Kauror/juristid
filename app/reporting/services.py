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
    archive,
    documents,
    historical,
    opinions,
    organisations,
    portfolio,
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
    keys.MATTERS_BY_RESPONSIBILITY: portfolio.matters_by_responsibility,
    keys.MATTERS_BY_YEAR_AND_RESPONSIBILITY: portfolio.matters_by_year_and_responsibility,
    keys.ACTIVE_FULL_MATTERS_BY_STAGE: portfolio.active_full_matters_by_stage,
    keys.ACTIVE_FULL_MATTERS_BY_RESPONSIBILITY: (portfolio.active_full_matters_by_responsibility),
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
    keys.NEW_NATIVE_FULL_MATTERS_BY_MONTH: portfolio.new_native_full_matters_by_month,
    keys.NEW_NATIVE_MATTERS_BY_RESPONSIBILITY_MONTH: (
        portfolio.new_native_matters_by_responsibility_month
    ),
    keys.NEW_NATIVE_MATTERS_YOY_CHANGE: portfolio.new_native_matters_yoy_change,
    keys.ACTIVE_WITHOUT_NEXT_ACTION: activity.active_without_next_action,
    keys.ACTIVE_WITHOUT_OWNER: activity.active_without_owner,
    keys.ACTIVE_WITHOUT_STAGE: activity.active_without_stage,
    keys.RESPONSE_DEADLINES_OPEN: activity.response_deadlines_open,
    keys.OVERDUE_DO_DEADLINE: activity.overdue_do_deadline,
    keys.REVIEW_DUE: activity.review_due,
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
    # Arvamuste arhiiv ajas — evidence trends, never canonical Submissions.
    keys.OPINION_ARCHIVE_BY_YEAR: archive.opinion_archive_by_year,
    keys.OPINION_ARCHIVE_BY_MONTH: archive.opinion_archive_by_month,
    keys.OPINION_ARCHIVE_YOY_CHANGE: archive.opinion_archive_yoy_change,
    keys.OPINION_ARCHIVE_LINK_COVERAGE: archive.opinion_archive_link_coverage,
    keys.OPINION_ARCHIVE_LINKED_BY_RESPONSIBILITY: (
        archive.opinion_archive_linked_by_responsibility
    ),
    keys.OPINION_ARCHIVE_LINKED_BY_MONTH_RESPONSIBILITY: (
        archive.opinion_archive_linked_by_month_and_responsibility
    ),
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
    """One tab's worth of answers, already computed.

    ``trends``, ``matrices`` and ``comparisons`` are separate lists rather than
    a flag on each result, because the view renders each with a different
    partial and a tab that had to sort its own results by shape would be one
    more place for two tabs to disagree.
    """

    cards: list[MetricResult] = field(default_factory=list)
    charts: list[MetricResult] = field(default_factory=list)
    tables: list[MetricResult] = field(default_factory=list)
    trends: list[MetricResult] = field(default_factory=list)
    matrices: list[MetricResult] = field(default_factory=list)
    comparisons: list[MetricResult] = field(default_factory=list)
    #: Extra named groups a single tab needs — the archive block on Koja
    #: tegevus, the portfolio and coverage blocks on Üldpilt.
    groups: dict[str, list[MetricResult]] = field(default_factory=dict)

    @property
    def all_results(self) -> list[MetricResult]:
        return [
            *self.cards,
            *self.charts,
            *self.tables,
            *self.trends,
            *self.matrices,
            *self.comparisons,
            *[result for group in self.groups.values() for result in group],
        ]


#: Üldpilt, in five groups: the headline counts, how much work there is over
#: time, how much archived advocacy there is over time, what the department is
#: holding right now, and how far the data reaches.
#:
#: Deliberately not every metric. A landing page that tries to say everything
#: says nothing, and every group here is a question somebody arrives with
#: (brief 11, 56).
OVERVIEW_CARDS = [
    keys.MATTERS_TOTAL,
    keys.ACTIVE_FULL_MATTERS,
    keys.NEW_NATIVE_FULL_MATTERS,
    keys.OPINION_ARCHIVE_DISTINCT_BINARIES,
    keys.SUBMISSIONS_SENT,
]

#: Period-over-period cards. Both sides of each are cut at the same date, and
#: neither direction is framed as good news (brief 33, 34).
OVERVIEW_COMPARISONS = [
    keys.NEW_NATIVE_MATTERS_YOY_CHANGE,
    keys.OPINION_ARCHIVE_YOY_CHANGE,
]

#: Two long trends with deliberately different starting years: the register
#: begins in 2011 and the opinions archive in 2020. Aligning them by drawing
#: nine years of archive zeros would be the false fact this workspace exists to
#: refuse (brief 43).
OVERVIEW_TRENDS = [
    keys.MATTERS_BY_REPORTING_YEAR,
    keys.OPINION_ARCHIVE_BY_YEAR,
]

#: What is on the department's desk today — the point-in-time pair.
OVERVIEW_PORTFOLIO = [
    keys.ACTIVE_FULL_MATTERS_BY_STAGE,
    keys.ACTIVE_FULL_MATTERS_BY_RESPONSIBILITY,
]

#: How far the data reaches. Coverage rather than achievement, and the wording
#: on each card says so.
OVERVIEW_COVERAGE = [
    keys.OPINION_ARCHIVE_LINK_COVERAGE,
    keys.MATTERS_WITH_HISTORICAL_SOURCE,
    keys.DATA_QUALITY_ATTENTION,
]

MATTERS_CARDS = [
    keys.MATTERS_TOTAL,
    keys.ACTIVE_FULL_MATTERS,
    keys.ONENOTE_ONLY_MATTERS,
    keys.MATTERS_UNCLASSIFIED_POLICY_AREA,
]

MATTERS_TRENDS = [
    keys.MATTERS_BY_REPORTING_YEAR,
    keys.NEW_NATIVE_FULL_MATTERS_BY_MONTH,
]

#: The two-dimensional views. Real tables, computed in Python, never a heat map
#: whose value is carried by a shade (brief 50).
MATTERS_MATRICES = [
    keys.MATTERS_BY_YEAR_AND_RESPONSIBILITY,
    keys.NEW_NATIVE_MATTERS_BY_RESPONSIBILITY_MONTH,
]

MATTERS_CHARTS = [
    keys.MATTERS_BY_RECORD_MODE,
    keys.MATTERS_BY_ORIGIN,
    keys.MATTERS_BY_STAGE,
    keys.MATTERS_BY_TRACK,
    keys.MATTERS_BY_POLICY_AREA,
    keys.MATTERS_BY_TAG,
    # `MATTERS_BY_RESPONSIBILITY` rather than `MATTERS_BY_OWNER`. The two
    # answer the same question and rendered side by side as two charts with
    # identical bars and near-identical Estonian titles — *vastutuse järgi* and
    # *vastutaja järgi* — which a browser screenshot made obvious and no
    # assertion would have. The one kept is the one that preserves a register
    # name this system has no account for instead of discarding it into
    # "Vastutaja määramata", which is the whole point of the dimension on a
    # reporting surface (brief 15, 55, 56).
    keys.MATTERS_BY_RESPONSIBILITY,
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

#: Canonical Submissions. This is the only trend on the tab that claims Koda
#: sent anything.
ACTIVITY_TRENDS = [
    keys.SUBMISSIONS_SENT_BY_PERIOD,
]

#: The archive's own history, kept in its own section with its own heading and
#: its own date basis. Beside the canonical metrics, never on top of them
#: (brief 39, 40).
ACTIVITY_ARCHIVE_TRENDS = [
    keys.OPINION_ARCHIVE_BY_YEAR,
    keys.OPINION_ARCHIVE_BY_MONTH,
]

ACTIVITY_ARCHIVE_CHARTS = [
    keys.OPINION_ARCHIVE_LINKED_BY_RESPONSIBILITY,
]

ACTIVITY_ARCHIVE_MATRICES = [
    keys.OPINION_ARCHIVE_LINKED_BY_MONTH_RESPONSIBILITY,
]

ACTIVITY_CHARTS = [
    keys.SUBMISSIONS_BY_KIND,
    keys.MATTERS_BY_SUBMISSION_COUNT,
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
    keys.OPINION_ARCHIVE_LINK_COVERAGE,
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
    keys.OPINION_ARCHIVE_LINK_COVERAGE,
    keys.HISTORICAL_SUBMISSION_COVERAGE,
    keys.SUBMISSION_RECIPIENT_COVERAGE,
    keys.OPINION_ARCHIVE_UNRESOLVED,
]

QUALITY_CHARTS = [
    keys.RECONCILIATION_BY_CLASS,
]


#: Every group of keys a tab renders, named once.
#:
#: The catalogue parity test reads this rather than restating the list, so a
#: new section on a tab cannot quietly escape the check that its keys exist —
#: which is exactly what happened the first time a section was added and the
#: test kept passing against the six groups it still knew about.
PAGE_GROUPS: tuple[tuple[str, list[str]], ...] = (
    ("OVERVIEW_CARDS", OVERVIEW_CARDS),
    ("OVERVIEW_COMPARISONS", OVERVIEW_COMPARISONS),
    ("OVERVIEW_TRENDS", OVERVIEW_TRENDS),
    ("OVERVIEW_PORTFOLIO", OVERVIEW_PORTFOLIO),
    ("OVERVIEW_COVERAGE", OVERVIEW_COVERAGE),
    ("MATTERS_CARDS", MATTERS_CARDS),
    ("MATTERS_TRENDS", MATTERS_TRENDS),
    ("MATTERS_MATRICES", MATTERS_MATRICES),
    ("MATTERS_CHARTS", MATTERS_CHARTS),
    ("MATTERS_TABLES", MATTERS_TABLES),
    ("ACTIVITY_CARDS", ACTIVITY_CARDS),
    ("ACTIVITY_TRENDS", ACTIVITY_TRENDS),
    ("ACTIVITY_ARCHIVE_TRENDS", ACTIVITY_ARCHIVE_TRENDS),
    ("ACTIVITY_ARCHIVE_CHARTS", ACTIVITY_ARCHIVE_CHARTS),
    ("ACTIVITY_ARCHIVE_MATRICES", ACTIVITY_ARCHIVE_MATRICES),
    ("ACTIVITY_CHARTS", ACTIVITY_CHARTS),
    ("ACTIVITY_TABLES", ACTIVITY_TABLES),
    ("HISTORICAL_CARDS", HISTORICAL_CARDS),
    ("HISTORICAL_CHARTS", HISTORICAL_CHARTS),
    ("HISTORICAL_TABLES", HISTORICAL_TABLES),
    ("QUALITY_CARDS", QUALITY_CARDS),
    ("QUALITY_CHARTS", QUALITY_CHARTS),
)


def overview_page(context: ReportingContext) -> Page:
    return Page(
        cards=compute_many(OVERVIEW_CARDS, context),
        comparisons=compute_many(OVERVIEW_COMPARISONS, context),
        trends=compute_many(OVERVIEW_TRENDS, context),
        charts=compute_many(OVERVIEW_PORTFOLIO, context),
        groups={"coverage": compute_many(OVERVIEW_COVERAGE, context)},
    )


def matters_page(context: ReportingContext) -> Page:
    return Page(
        cards=compute_many(MATTERS_CARDS, context),
        trends=compute_many(MATTERS_TRENDS, context),
        matrices=compute_many(MATTERS_MATRICES, context),
        charts=compute_many(MATTERS_CHARTS, context),
        tables=compute_many(MATTERS_TABLES, context),
    )


def activity_page(context: ReportingContext) -> Page:
    return Page(
        cards=compute_many(ACTIVITY_CARDS, context),
        trends=compute_many(ACTIVITY_TRENDS, context),
        charts=compute_many(ACTIVITY_CHARTS, context),
        tables=compute_many(ACTIVITY_TABLES, context),
        groups={
            "archive_trends": compute_many(ACTIVITY_ARCHIVE_TRENDS, context),
            "archive_charts": compute_many(ACTIVITY_ARCHIVE_CHARTS, context),
            "archive_matrices": compute_many(ACTIVITY_ARCHIVE_MATRICES, context),
        },
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
