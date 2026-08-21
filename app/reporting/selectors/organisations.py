"""Who Koda deals with, kept in the direction the relationship actually ran.

Four different relationships exist between a Matter and an organisation, and
this module refuses to merge any of them:

* ``Matter.source_organisation`` — who sent it, or who initiated it;
* ``Matter.addressee_organisation`` — who it was addressed to;
* ``SubmissionRecipient`` — who Koda formally wrote to (``selectors.submissions``);
* ``SubmissionJointSubmitter`` — who co-signed.

A single chart called "ministeeriumid teemade kaupa" would be four different
questions wearing one label, and nobody reading it could tell which one they
were looking at (Stage-2E brief 27).

The register makes this worse rather than better, and the statistics have to
say so: the workbook's single counterparty column meant **KELLELT — the
sender** in 2011–2019 and **KELLELE — the addressee** from 2020. The importer
already resolved that into the two separate columns above; what remains is to
label the resulting coverage honestly, because the sender column is nearly
empty after 2020 and the addressee column is entirely empty before it. That is
an era boundary, not missing data.
"""

from __future__ import annotations

from app.reporting import metric_catalogue as keys
from app.reporting.context import ReportingContext
from app.reporting.metric_catalogue import definition
from app.reporting.metric_types import MetricResult, Segment
from app.reporting.selectors.base import (
    count,
    grouped_count,
    population_for,
    register_url,
    simple_result,
    top_segments,
)


def _by_organisation(
    context: ReportingContext, key: str, field: str, note: str, parameter: str
) -> MetricResult:
    spec = definition(key)
    population = population_for(context, spec)
    population_count = count(population)

    named = count(population.filter(**{f"{field}__isnull": False}))
    rows = (
        population.filter(**{f"{field}__isnull": False})
        .values(f"{field}__id", f"{field}__name")
        .annotate(total=grouped_count())
        .order_by("-total", f"{field}__name")
    )
    segments = top_segments(
        [
            Segment(
                label=row[f"{field}__name"],
                value=row["total"],
                url=register_url(context, **{parameter: str(row[f"{field}__id"])}),
                note=note,
            )
            for row in rows
        ],
    )

    return simple_result(
        spec,
        context=context,
        value=named,
        population_count=population_count,
        eligible_count=named,
        coverage_count=named,
        coverage_denominator=population_count,
        segments=segments,
        url=register_url(context),
        notes=(spec.source_era_limitations_et,),
    )


def matters_by_source_organisation(context: ReportingContext) -> MetricResult:
    """Who the matter came from. `KELLELT` — the sender, 2011–2019."""
    return _by_organisation(
        context,
        keys.MATTERS_BY_SOURCE_ORGANISATION,
        "source_organisation",
        "Algataja või saatja",
        "saatja",
    )


def matters_by_addressee_organisation(context: ReportingContext) -> MetricResult:
    """Who the matter was addressed to. `KELLELE` — the addressee, 2020–2026."""
    return _by_organisation(
        context,
        keys.MATTERS_BY_ADDRESSEE_ORGANISATION,
        "addressee_organisation",
        "Adressaat",
        "adressaat",
    )
