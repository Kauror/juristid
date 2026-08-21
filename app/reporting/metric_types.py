"""What a metric *is*, and what an answer to one looks like.

Nothing in this module queries anything. It is the contract that every number
in the Statistika workspace has to satisfy before it may appear on a page, and
keeping it free of database access is what lets the catalogue be read as a
document rather than traced through a call graph.

Three ideas, and the separation between them is the point.

``MetricDefinition`` is the question, written down once: which records are
eligible, which clock the period is measured on, what "complete" would mean,
and which era the source can honestly speak for. It is a frozen dataclass in
code — not a table — because a definition that can be edited through a screen
is a definition nobody reviewed (docs/adr/0007, master specification 18.5).

``MetricResult`` is the answer, and it always carries its own population and
coverage. A bare integer is exactly the artefact this product exists to stop
producing: 2 455 is not a fact until somebody says 2 455 *of what*.

``MetricStatus`` is how an honest metric declines. When the source cannot
support a number, the result says ``INSUFFICIENT_DATA`` or ``NOT_APPLICABLE``
rather than returning the zero a reader will take for a measurement
(Stage-2E brief 9, 24).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime

from django.db import models


class MetricStatus(models.TextChoices):
    """Whether the number may be read as a measurement.

    ``INSUFFICIENT_DATA`` and ``NOT_APPLICABLE`` are different failures and are
    never collapsed. The first means the records exist but too few of them
    carry the field — look again when the data improves. The second means the
    question does not apply to this population at all, and looking again will
    never change that: a signed container has no extracted text because nothing
    will ever open it, not because a parser has not got round to it yet.
    """

    AVAILABLE = "AVAILABLE", "Arvutatud"
    PARTIAL = "PARTIAL", "Osaline katvus"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA", "Ebapiisavad andmed"
    NOT_APPLICABLE = "NOT_APPLICABLE", "Ei kohaldu"


class TimeBasis(models.TextChoices):
    """Which clock a metric's period filter is read against.

    "Periood" is not one field, and pretending it is was one of the register's
    real defects. A Matter's reporting year, the day an opinion was sent, the
    day a lawyer says a meeting happened and the timestamp a OneNote page
    carries are four different facts, and a filter that silently used whichever
    column was handy would produce four different answers to the same question
    (Stage-2E brief 14).

    A database ``created_at`` basis is deliberately absent. The row for a 2014
    register matter was written in 2026, and a year axis built on that would
    report the whole archive as this year's work.
    """

    REPORTING_YEAR = "REPORTING_YEAR", "Aruandlusaasta"
    RECEIVED_DATE = "RECEIVED_DATE", "Saabumise kuupäev"
    SUBMISSION_SENT_AT = "SUBMISSION_SENT_AT", "Arvamuse saatmise aeg"
    ENTRY_OCCURRED_AT = "ENTRY_OCCURRED_AT", "Sissekande toimumise aeg"
    SOURCE_TIMESTAMP = "SOURCE_TIMESTAMP", "Lähteallika ajatempel"
    SNAPSHOT_DATE = "SNAPSHOT_DATE", "Hetktõmmise kuupäev"
    #: Answered as of now, not over a window. "How many are open" has no period.
    POINT_IN_TIME = "POINT_IN_TIME", "Hetkeseis"
    #: The whole archive, on purpose. Labelled as such wherever it is shown, so
    #: a period-filtered card and an archive-wide card never sit side by side
    #: looking alike (Stage-2E brief 59).
    WHOLE_CORPUS = "WHOLE_CORPUS", "Kogu korpus"


class Unit(models.TextChoices):
    COUNT = "COUNT", "kirjet"
    MATTERS = "MATTERS", "teemat"
    SUBMISSIONS = "SUBMISSIONS", "arvamust"
    PAGES = "PAGES", "lehte"
    FILES = "FILES", "faili"
    BYTES = "BYTES", "baiti"
    PERCENT = "PERCENT", "%"


#: A definition that names no record modes or origins accepts all of them. An
#: empty tuple is "no restriction", never "nothing qualifies" — stated here
#: because the opposite reading would silently empty every metric.
ALL: tuple[str, ...] = ()


@dataclass(frozen=True)
class MetricDefinition:
    """One versioned, code-reviewed metric definition.

    ``version`` is bumped whenever the population, the time basis or the
    eligibility changes. It travels on every result and into every export, so a
    number quoted in a report can be traced to the rule that produced it rather
    than to whatever the rule happens to be today.
    """

    key: str
    version: int
    label_et: str
    description_et: str

    #: Which records are eligible at all, in one sentence a lawyer can check.
    source_population_et: str
    time_basis: TimeBasis
    unit: Unit = Unit.COUNT

    #: Enforced by the selectors through ``eligible_matters``, not merely
    #: documented. A definition nothing reads is a comment.
    eligible_record_modes: tuple[str, ...] = ALL
    eligible_origins: tuple[str, ...] = ALL

    #: Fields a record must carry to be counted. Records missing them are the
    #: difference between the population and the coverage.
    required_fields: tuple[str, ...] = ()
    exclusions_et: str = ""

    #: The first year this metric may be published for. A trend line does not
    #: start before it: an absent measurement is not a zero (brief 24).
    earliest_reliable_period: int | None = None
    source_era_limitations_et: str = ""

    #: Below either threshold the result is INSUFFICIENT_DATA rather than a
    #: precise-looking figure (master specification 18.5).
    minimum_population: int = 0
    minimum_coverage: float | None = None

    coverage_description_et: str = ""
    drillthrough_et: str = ""
    notes_et: str = ""

    #: Whether the selected period narrows this metric. Archive-wide totals set
    #: this to False and say so on the card.
    respects_period: bool = True

    def __post_init__(self) -> None:
        if self.minimum_coverage is not None and not 0.0 <= self.minimum_coverage <= 1.0:
            raise ValueError(f"{self.key}: minimum_coverage must be a fraction between 0 and 1.")
        if self.version < 1:
            raise ValueError(f"{self.key}: version starts at 1.")

    @property
    def qualified_key(self) -> str:
        """``KEY@version`` — what an export column and an audit trail record."""
        return f"{self.key}@{self.version}"

    def accepts_record_mode(self, value: str) -> bool:
        return not self.eligible_record_modes or value in self.eligible_record_modes

    def accepts_origin(self, value: str) -> bool:
        return not self.eligible_origins or value in self.eligible_origins


@dataclass(frozen=True)
class Segment:
    """One bar, one row, one slice — with the records behind it.

    ``url`` is not decoration. Every segment opens the exact authorized
    population it counted, and a test asserts the two agree (brief 38, 66).
    """

    label: str
    value: int
    url: str = ""
    note: str = ""
    #: True for "Teadmata aasta", "Klassifitseerimata", "Vastutaja määramata".
    #: Rendered differently and never dropped from the denominator (brief 42).
    is_unknown: bool = False

    def share_of(self, total: int) -> float:
        return (self.value / total) if total else 0.0


@dataclass(frozen=True)
class Distribution:
    """Order statistics for a skewed count, because a mean would mislead.

    Materials per Matter is the canonical example: most have a handful and one
    has four hundred. The arithmetic mean describes neither
    (master specification 18.10, brief 35).
    """

    n: int
    median: float
    p75: float
    p90: float
    p95: float
    maximum: int
    total: int

    @property
    def is_empty(self) -> bool:
        return self.n == 0


@dataclass(frozen=True)
class MetricResult:
    """One computed answer, carrying everything needed to read it honestly."""

    definition: MetricDefinition
    value: int = 0

    #: Records the definition considers in scope before any completeness test.
    population_count: int = 0
    #: Records that survived the period and the required-field tests.
    eligible_count: int = 0

    #: The completeness pair. ``coverage_denominator`` is the population the
    #: percentage is honest about; a denominator of zero is *not* 0 %.
    coverage_count: int | None = None
    coverage_denominator: int | None = None

    status: MetricStatus = MetricStatus.AVAILABLE
    period_start: date | None = None
    period_end: date | None = None
    as_of: datetime | None = None

    drillthrough_url: str = ""
    segments: tuple[Segment, ...] = field(default_factory=tuple)
    distribution: Distribution | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    # -- reading the answer ------------------------------------------------

    @property
    def key(self) -> str:
        return self.definition.key

    @property
    def definition_version(self) -> int:
        return self.definition.version

    @property
    def time_basis_label(self) -> str:
        return TimeBasis(self.definition.time_basis).label

    @property
    def has_value(self) -> bool:
        """Whether a number may be printed at all.

        The template asks this rather than testing the status itself, so that
        adding a fourth status cannot accidentally start rendering a figure the
        product decided not to stand behind.
        """
        return self.status in (MetricStatus.AVAILABLE, MetricStatus.PARTIAL)

    @property
    def coverage_percentage(self) -> float | None:
        if not self.coverage_denominator:
            return None
        count = self.coverage_count or 0
        return 100.0 * count / self.coverage_denominator

    @property
    def missing_from_coverage(self) -> int:
        if self.coverage_denominator is None:
            return 0
        return self.coverage_denominator - (self.coverage_count or 0)

    @property
    def segment_total(self) -> int:
        return sum(segment.value for segment in self.segments)

    @property
    def largest_segment(self) -> int:
        return max((segment.value for segment in self.segments), default=0)

    def with_note(self, note: str) -> MetricResult:
        return replace(self, notes=(*self.notes, note))


def grade(
    definition: MetricDefinition,
    *,
    population_count: int,
    coverage_count: int | None = None,
    coverage_denominator: int | None = None,
) -> MetricStatus:
    """Decide a result's status from its own thresholds.

    One function, so that "when is a number publishable" is answered in a single
    place. Every metric routes through it, including the ones with no coverage
    dimension, because a metric that grades itself is a metric that can quietly
    stop applying its own minimum.
    """
    if population_count < definition.minimum_population:
        return MetricStatus.INSUFFICIENT_DATA

    if definition.minimum_coverage is None or not coverage_denominator:
        return MetricStatus.AVAILABLE

    fraction = (coverage_count or 0) / coverage_denominator
    if fraction < definition.minimum_coverage:
        return MetricStatus.INSUFFICIENT_DATA
    if fraction < 1.0:
        return MetricStatus.PARTIAL
    return MetricStatus.AVAILABLE


def distribution_from(values: list[int]) -> Distribution:
    """Median and percentiles by nearest rank, on an already-materialised list.

    Nearest rank rather than interpolation: these are counts of files and of
    characters, so the p90 of a set of integers should be one of them rather
    than a number that never occurred.
    """
    if not values:
        return Distribution(n=0, median=0.0, p75=0.0, p90=0.0, p95=0.0, maximum=0, total=0)

    ordered = sorted(values)
    count = len(ordered)

    def at(fraction: float) -> float:
        index = min(count, max(1, math.ceil(fraction * count)))
        return float(ordered[index - 1])

    if count % 2:
        median = float(ordered[count // 2])
    else:
        median = (ordered[count // 2 - 1] + ordered[count // 2]) / 2

    return Distribution(
        n=count,
        median=median,
        p75=at(0.75),
        p90=at(0.90),
        p95=at(0.95),
        maximum=ordered[-1],
        total=sum(ordered),
    )
