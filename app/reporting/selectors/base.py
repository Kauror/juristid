"""The authorized populations every other selector starts from.

**Authorization precedes aggregation, everywhere, without exception.** Every
function in this package begins at ``Matter.objects.visible_to(viewer)`` — or at
a child queryset scoped through its Matter — and only then filters, groups,
counts, ranks, slices or exports. A restricted Matter therefore contributes
nothing to a total, a year bar, an owner tally, a coverage denominator, an
"other" bucket, a CSV row or a byte count.

The failure mode this rules out is specific and easy to miss: counting
everything and hiding rows at render time leaves the hidden rows *inside* the
numbers. Nothing on the screen looks wrong. The count is the disclosure
(master specification 5.2, 18.5, docs/adr/0005).

Two mechanical points that keep it true.

``Matter.objects.visible_to`` collapses a many-to-many join with ``distinct()``,
and the narrowing here can add more joins — a tag, a policy area, a source page.
So every count in this package is either ``distinct().count()`` or
``Count("id", distinct=True)``. A plain ``Count`` over a fanned-out join is not
a smaller mistake for being an arithmetic one.

An unreadable filter empties the population rather than being ignored. A URL
naming an owner id that is not a UUID must not produce a department-wide total
underneath a chip that says one person's name.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django.db.models import Count, Q, QuerySet
from django.urls import reverse

from app.matters.enums import RecordMode
from app.matters.models import Matter
from app.matters.selectors import UNKNOWN_YEAR, register_year_q, unknown_register_year_q
from app.reporting.context import ReportingContext
from app.reporting.metric_types import (
    Distribution,
    MetricDefinition,
    MetricResult,
    MetricStatus,
    Segment,
    TimeBasis,
    grade,
)

#: How many bars a composition chart shows before the tail is grouped. The
#: grouped remainder is a real, clickable bucket rather than a silent truncation
#: — a chart that quietly drops its tail reads as complete when it is not
#: (Stage-2E brief, "no silent caps").
TOP_N = 12


def count(queryset: QuerySet[Any]) -> int:
    """A count that a join cannot inflate."""
    return queryset.distinct().count()


def grouped_count() -> Count:
    """``Count("id", distinct=True)`` — the only counter used for grouping."""
    return Count("id", distinct=True)


def grouped_count_over(path: str) -> Count:
    """The same, over a related path, for per-row counts a join could inflate."""
    return Count(path, distinct=True)


# ---------------------------------------------------------------------------
# Matter populations
# ---------------------------------------------------------------------------


def visible_matters(context: ReportingContext) -> QuerySet[Matter]:
    """Every Matter this viewer may read, narrowed by the active filters.

    The period is deliberately *not* applied here. Which field a period means
    depends on the metric's time basis, so applying it at this level would force
    every metric onto the Matter's reporting year — including the ones measured
    on a submission's send date (brief 14).
    """
    queryset = Matter.objects.visible_to(context.viewer)

    if context.owner_unreadable:
        return queryset.none()

    if context.record_mode:
        queryset = queryset.filter(record_mode=context.record_mode)
    if context.origin:
        queryset = queryset.filter(origin=context.origin)
    if context.owner_id is not None:
        queryset = queryset.filter(owner_id=context.owner_id)
    if context.policy_area_key:
        queryset = queryset.filter(policy_areas__key=context.policy_area_key)
    if context.stage_key:
        queryset = queryset.filter(stage__key=context.stage_key)
    if context.track:
        queryset = queryset.filter(track=context.track)
    if context.tag_key:
        queryset = queryset.filter(tags__key=context.tag_key)

    return queryset


def eligible_matters(context: ReportingContext, definition: MetricDefinition) -> QuerySet[Matter]:
    """The visible population, narrowed by the definition's own eligibility.

    The definition's ``eligible_record_modes`` and ``eligible_origins`` are read
    here rather than restated in each metric, which is what makes the catalogue
    load-bearing instead of documentary.
    """
    queryset = visible_matters(context)
    if definition.eligible_record_modes:
        queryset = queryset.filter(record_mode__in=definition.eligible_record_modes)
    if definition.eligible_origins:
        queryset = queryset.filter(origin__in=definition.eligible_origins)
    return queryset


def in_reporting_year(queryset: QuerySet[Matter], context: ReportingContext) -> QuerySet[Matter]:
    """Narrow to the period, on the Matter's *register* reporting year.

    Matters whose year is not a register year — the OneNote-only ones — fall out
    of every year-bounded population. That is correct rather than convenient:
    their ``reporting_year`` is a page timestamp, and letting it stand in for a
    reporting year is the conflation the brief forbids. The count that fell out
    is reported as a note beside the metric rather than disappearing.
    """
    start, end = context.period.start_year, context.period.end_year
    if start is None or end is None:
        return queryset
    return queryset.filter(register_year_q(start=start, end=end))


def unknown_year_matters(queryset: QuerySet[Matter]) -> QuerySet[Matter]:
    return queryset.filter(unknown_register_year_q())


def in_received_date(queryset: QuerySet[Matter], context: ReportingContext) -> QuerySet[Matter]:
    """Narrow to the period on the day the material actually arrived."""
    if context.period.is_all:
        return queryset
    return queryset.filter(
        received_date__gte=context.period.start_date,
        received_date__lte=context.period.end_date,
    )


#: Which narrowing each Matter time basis implies. A metric whose basis is not
#: listed is not narrowed by the period at all, which is why POINT_IN_TIME and
#: WHOLE_CORPUS metrics need no special case anywhere else.
_MATTER_PERIOD_FILTERS = {
    TimeBasis.REPORTING_YEAR.value: in_reporting_year,
    TimeBasis.RECEIVED_DATE.value: in_received_date,
}


def population_for(context: ReportingContext, definition: MetricDefinition) -> QuerySet[Matter]:
    """The eligible Matter population with the definition's period applied.

    Reading the period rule off the definition, rather than restating it per
    metric, is what stops two metrics on the same page silently measuring
    different clocks.
    """
    queryset = eligible_matters(context, definition)
    if not definition.respects_period:
        return queryset
    narrow = _MATTER_PERIOD_FILTERS.get(definition.time_basis)
    return queryset if narrow is None else narrow(queryset, context)


def active_full(context: ReportingContext) -> QuerySet[Matter]:
    """Open FULL Matters: the operational population, never the archive.

    Counting a decade of imported register rows as "active" would make every
    operational number meaningless, which is why this is one named function that
    the dashboard, the snapshots and the statistics all call.
    """
    return visible_matters(context).filter(is_open=True, record_mode=RecordMode.FULL)


# ---------------------------------------------------------------------------
# Drill-through links
# ---------------------------------------------------------------------------


def register_url(context: ReportingContext, **overrides: str) -> str:
    """A link into the Teemad register carrying the same filters.

    Reusing the register rather than building a second table is what keeps the
    promise behind every number: the count and the list come from one set of
    filters instead of two similar definitions that drift (brief 39).

    ``olek=koik`` is passed explicitly on every link. The register defaults to
    open records only, so a statistic over all Matters that linked without it
    would open a shorter list than the number it came from — the exact
    inconsistency the drill-through exists to rule out.
    """
    params: dict[str, str] = {"olek": "koik"}

    if context.record_mode:
        params["liik"] = context.record_mode
    if context.origin:
        params["paritolu"] = context.origin
    if context.owner_id is not None:
        params["vastutaja"] = str(context.owner_id)
    if context.policy_area_key:
        params["valdkond"] = context.policy_area_key
    if context.stage_key:
        params["hetkeseis"] = context.stage_key
    if context.track:
        params["menetlusliik"] = context.track
    if context.tag_key:
        params["silt"] = context.tag_key

    if not context.period.is_all:
        start, end = context.period.start_year, context.period.end_year
        params["aasta"] = str(start) if start == end else f"{start}-{end}"

    for key, value in overrides.items():
        if value:
            params[key] = value
        else:
            params.pop(key, None)

    return f"{reverse('matters:matter_list')}?{urlencode(params)}"


def unknown_year_url(context: ReportingContext) -> str:
    return register_url(context, aasta=UNKNOWN_YEAR)


# ---------------------------------------------------------------------------
# Assembling a result
# ---------------------------------------------------------------------------


def simple_result(
    definition: MetricDefinition,
    *,
    context: ReportingContext,
    value: int,
    population_count: int | None = None,
    eligible_count: int | None = None,
    coverage_count: int | None = None,
    coverage_denominator: int | None = None,
    url: str = "",
    segments: tuple[Segment, ...] = (),
    distribution: Distribution | None = None,
    notes: tuple[str, ...] = (),
    status: MetricStatus | None = None,
) -> MetricResult:
    """Wrap a computed number in its definition, period and grade.

    One constructor so that no metric can accidentally publish itself without a
    status: ``grade`` applies the definition's own thresholds unless a caller
    has already decided the answer is ``NOT_APPLICABLE``.
    """
    population = population_count if population_count is not None else value
    eligible = eligible_count if eligible_count is not None else value

    resolved = status or grade(
        definition,
        population_count=population,
        coverage_count=coverage_count,
        coverage_denominator=coverage_denominator,
    )

    return MetricResult(
        definition=definition,
        value=value,
        population_count=population,
        eligible_count=eligible,
        coverage_count=coverage_count,
        coverage_denominator=coverage_denominator,
        status=resolved,
        period_start=context.period.start_date if definition.respects_period else None,
        period_end=context.period.end_date if definition.respects_period else None,
        as_of=context.now,
        drillthrough_url=url,
        segments=segments,
        distribution=distribution,
        notes=notes,
    )


def top_segments(
    rows: list[Segment],
    *,
    limit: int = TOP_N,
    remainder_label: str = "Muud",
    remainder_url: str = "",
) -> tuple[Segment, ...]:
    """Keep the largest ``limit`` segments and make the tail an explicit row.

    The remainder is labelled and counted. A chart that shows twelve bars out of
    forty without saying so reads as the whole picture, and the reader has no
    way to tell.
    """
    if len(rows) <= limit:
        return tuple(rows)

    head = rows[:limit]
    tail = rows[limit:]
    remainder = sum(segment.value for segment in tail)
    note = f"{len(tail)} muud rühma"
    return (*head, Segment(label=remainder_label, value=remainder, url=remainder_url, note=note))


def coverage_note(missing: int, what: str) -> tuple[str, ...]:
    """One sentence about what the number could not include, or nothing."""
    if missing <= 0:
        return ()
    return (f"{missing} {what}",)


def exclude_q(queryset: QuerySet[Any], condition: Q) -> QuerySet[Any]:
    return queryset.exclude(condition)
