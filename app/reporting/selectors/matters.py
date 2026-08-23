"""Matter-population metrics: how many, of what kind, whose, and about what.

Three rules from the brief live here rather than in a document.

**A year on this page is a reporting year.** Never ``created_at``: the database
row for a 2014 register matter was written in 2026. Never a OneNote page's
timestamp either — those Matters land in *Teadmata aasta* and their source
dates are analysed separately (``selectors.historical``).

**Unknown is data.** *Teadmata aasta*, *Klassifitseerimata*, *Vastutaja
määramata* and *Hetkeseis määramata* are rows in the chart with links behind
them, not records quietly dropped from a denominator. A composition that omits
its unclassified tail reports a coverage it does not have (brief 20, 42).

**Owner counts are inventory.** Not workload, not productivity, not a ranking.
A count of open files says nothing about effort — one is a two-line monitoring
note and the next is a year of consultation — and the specification forbids
presenting it as if it did (18.8).
"""

from __future__ import annotations

from django.db.models import Count, Exists, OuterRef

from app.legacy_import.source_pages import MatterSourcePage
from app.matters.enums import MatterOrigin, RecordMode
from app.matters.selectors import MISSING
from app.reporting import metric_catalogue as keys
from app.reporting.context import ReportingContext
from app.reporting.metric_catalogue import definition
from app.reporting.metric_types import MetricResult, Segment
from app.reporting.selectors.base import (
    corpus_url,
    count,
    eligible_matters,
    grouped_count,
    in_reporting_year,
    known_year_q,
    population_for,
    register_url,
    simple_result,
    top_segments,
    unknown_year_url,
)
from app.workflow.enums import Track

UNKNOWN_YEAR_LABEL = "Teadmata aasta"
UNCLASSIFIED_LABEL = "Klassifitseerimata"
NO_OWNER_LABEL = "Vastutaja määramata"
NO_STAGE_LABEL = "Hetkeseis määramata"
NO_TRACK_LABEL = "Menetlusliik määramata"


def _has_source_page() -> Exists:
    return Exists(MatterSourcePage.objects.filter(matter=OuterRef("pk")))


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------


def matters_total(context: ReportingContext) -> MetricResult:
    """How many Matters the period contains, and how many it could not place."""
    spec = definition(keys.MATTERS_TOTAL)
    population = eligible_matters(context, spec)
    population_count = count(population)
    with_year = count(population.filter(known_year_q()))

    value = (
        population_count if context.period.is_all else count(in_reporting_year(population, context))
    )

    notes: tuple[str, ...] = ()
    missing = population_count - with_year
    if missing and not context.period.is_all:
        notes = (
            f"{missing} teemal ei ole registri aruandlusaastat, seega ei saa neid "
            f"perioodi paigutada. Nad on rühmas „{UNKNOWN_YEAR_LABEL}“.",
        )

    return simple_result(
        spec,
        context=context,
        value=value,
        population_count=population_count,
        eligible_count=value,
        coverage_count=with_year,
        coverage_denominator=population_count,
        url=register_url(context),
        notes=notes,
    )


def active_full_matters(context: ReportingContext) -> MetricResult:
    """Open FULL Matters right now. The archive is not active work."""
    spec = definition(keys.ACTIVE_FULL_MATTERS)
    population = eligible_matters(context, spec).filter(is_open=True)
    total = count(population)
    return simple_result(
        spec,
        context=context,
        value=total,
        url=register_url(context, olek="avatud", liik=RecordMode.FULL.value, aasta=""),
    )


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def matters_by_reporting_year(context: ReportingContext) -> MetricResult:
    """The year axis, with an explicit bucket for the years nobody can supply.

    Only years that actually carry records become bars. Drawing an empty 2013
    would say Koda did nothing that year, when what is true is that this
    population has nothing in it (brief 24).
    """
    spec = definition(keys.MATTERS_BY_REPORTING_YEAR)
    population = eligible_matters(context, spec)
    population_count = count(population)

    dated = population.filter(known_year_q())
    if not context.period.is_all:
        dated = in_reporting_year(dated, context)

    rows = dated.values("reporting_year").annotate(total=grouped_count()).order_by("reporting_year")
    segments = [
        Segment(
            label=str(row["reporting_year"]),
            value=row["total"],
            url=register_url(context, aasta=str(row["reporting_year"])),
        )
        for row in rows
    ]

    unknown = count(population.exclude(known_year_q()))
    notes: tuple[str, ...] = ()
    if context.period.is_all:
        if unknown:
            segments.append(
                Segment(
                    label=UNKNOWN_YEAR_LABEL,
                    value=unknown,
                    url=unknown_year_url(context),
                    note="Peamiselt OneNote'i-põhised teemad",
                    is_unknown=True,
                )
            )
    elif unknown:
        notes = (
            f"{unknown} teemat on väljaspool aastatelge, sest registri aruandlusaastat ei ole.",
        )

    covered = count(population.filter(known_year_q()))
    return simple_result(
        spec,
        context=context,
        value=sum(segment.value for segment in segments),
        population_count=population_count,
        eligible_count=sum(segment.value for segment in segments),
        coverage_count=covered,
        coverage_denominator=population_count,
        url=register_url(context),
        segments=tuple(segments),
        notes=notes,
    )


def matters_by_record_mode(context: ReportingContext) -> MetricResult:
    spec = definition(keys.MATTERS_BY_RECORD_MODE)
    population = population_for(context, spec)
    labels = dict(RecordMode.choices)
    rows = population.values("record_mode").annotate(total=grouped_count()).order_by("-total")
    segments = tuple(
        Segment(
            label=labels.get(row["record_mode"], row["record_mode"]),
            value=row["total"],
            url=register_url(context, liik=row["record_mode"]),
        )
        for row in rows
    )
    return simple_result(
        spec,
        context=context,
        value=count(population),
        segments=segments,
        url=register_url(context),
    )


def matters_by_origin(context: ReportingContext) -> MetricResult:
    spec = definition(keys.MATTERS_BY_ORIGIN)
    population = population_for(context, spec)
    labels = dict(MatterOrigin.choices)
    rows = population.values("origin").annotate(total=grouped_count()).order_by("-total")
    segments = tuple(
        Segment(
            label=labels.get(row["origin"], row["origin"]),
            value=row["total"],
            url=register_url(context, paritolu=row["origin"]),
        )
        for row in rows
    )
    return simple_result(
        spec,
        context=context,
        value=count(population),
        segments=segments,
        url=register_url(context),
    )


def matters_by_stage(context: ReportingContext) -> MetricResult:
    """Where the external process stands. Stage only, never a closure reason."""
    spec = definition(keys.MATTERS_BY_STAGE)
    population = population_for(context, spec)
    population_count = count(population)

    rows = (
        population.filter(stage__isnull=False)
        .values("stage__key", "stage__label_et", "stage__sort_order")
        .annotate(total=grouped_count())
        .order_by("stage__sort_order")
    )
    segments = [
        Segment(
            label=row["stage__label_et"],
            value=row["total"],
            url=register_url(context, hetkeseis=row["stage__key"]),
        )
        for row in rows
    ]
    classified = sum(segment.value for segment in segments)

    without = population_count - classified
    if without:
        segments.append(
            Segment(
                label=NO_STAGE_LABEL,
                value=without,
                url=register_url(context, hetkeseis=MISSING),
                note="Enamasti arhiivikirjed",
                is_unknown=True,
            )
        )

    return simple_result(
        spec,
        context=context,
        value=population_count,
        population_count=population_count,
        coverage_count=classified,
        coverage_denominator=population_count,
        segments=tuple(segments),
        url=register_url(context),
    )


def matters_by_owner(context: ReportingContext) -> MetricResult:
    """Portfolio inventory per person. Never a ranking of people."""
    spec = definition(keys.MATTERS_BY_OWNER)
    population = population_for(context, spec)
    population_count = count(population)

    rows = (
        population.filter(owner__isnull=False)
        .values("owner_id", "owner__display_name")
        .annotate(total=grouped_count())
        .order_by("owner__display_name")
    )
    segments = [
        Segment(
            label=row["owner__display_name"],
            value=row["total"],
            url=register_url(context, vastutaja=str(row["owner_id"])),
        )
        for row in rows
    ]
    assigned = sum(segment.value for segment in segments)

    unassigned = population_count - assigned
    if unassigned:
        segments.append(
            Segment(
                label=NO_OWNER_LABEL,
                value=unassigned,
                url=register_url(context, vastutaja=MISSING),
                is_unknown=True,
            )
        )

    return simple_result(
        spec,
        context=context,
        value=population_count,
        population_count=population_count,
        coverage_count=assigned,
        coverage_denominator=population_count,
        segments=tuple(segments),
        url=register_url(context),
    )


def matters_by_policy_area(context: ReportingContext) -> MetricResult:
    """Canonical classification, with its unclassified tail kept in view.

    A Matter in two areas counts in both bars, so the bars sum to more than the
    population. That is the honest arithmetic of a many-to-many classification,
    and the coverage line beneath states the population it was measured over.
    """
    spec = definition(keys.MATTERS_BY_POLICY_AREA)
    population = population_for(context, spec)
    population_count = count(population)

    classified = count(population.filter(policy_areas__isnull=False))
    rows = (
        population.filter(policy_areas__isnull=False)
        .values("policy_areas__key", "policy_areas__name_et")
        .annotate(total=grouped_count())
        .order_by("-total", "policy_areas__name_et")
    )
    segments = [
        Segment(
            label=row["policy_areas__name_et"],
            value=row["total"],
            url=register_url(context, valdkond=row["policy_areas__key"]),
        )
        for row in rows
    ]
    segments = list(top_segments(segments))

    unclassified = population_count - classified
    if unclassified:
        segments.append(
            Segment(
                label=UNCLASSIFIED_LABEL,
                value=unclassified,
                url=register_url(context, valdkond=MISSING),
                note="Sageli vanemad arhiivikirjed",
                is_unknown=True,
            )
        )

    return simple_result(
        spec,
        context=context,
        value=classified,
        population_count=population_count,
        eligible_count=classified,
        coverage_count=classified,
        coverage_denominator=population_count,
        segments=tuple(segments),
        # No metric-level link: "Matters carrying at least one of these" is not
        # a filter the register has, and every bar below links exactly. A link
        # that opened the whole population would contradict the number above it.
        url="",
    )


def matters_unclassified_policy_area(context: ReportingContext) -> MetricResult:
    spec = definition(keys.MATTERS_UNCLASSIFIED_POLICY_AREA)
    population = population_for(context, spec)
    population_count = count(population)
    unclassified = count(population.filter(policy_areas__isnull=True))
    return simple_result(
        spec,
        context=context,
        value=unclassified,
        population_count=population_count,
        url=register_url(context, valdkond=MISSING),
    )


def matters_by_track(context: ReportingContext) -> MetricResult:
    spec = definition(keys.MATTERS_BY_TRACK)
    population = population_for(context, spec)
    population_count = count(population)
    labels = dict(Track.choices)

    rows = (
        population.exclude(track="")
        .values("track")
        .annotate(total=grouped_count())
        .order_by("-total")
    )
    segments = [
        Segment(
            label=labels.get(row["track"], row["track"]),
            value=row["total"],
            url=register_url(context, menetlusliik=row["track"]),
        )
        for row in rows
    ]
    classified = sum(segment.value for segment in segments)

    if population_count - classified:
        segments.append(
            Segment(
                label=NO_TRACK_LABEL,
                value=population_count - classified,
                url=register_url(context, menetlusliik=MISSING),
                is_unknown=True,
            )
        )

    return simple_result(
        spec,
        context=context,
        value=classified,
        population_count=population_count,
        coverage_count=classified,
        coverage_denominator=population_count,
        segments=tuple(segments),
        # No metric-level link: "Matters carrying at least one of these" is not
        # a filter the register has, and every bar below links exactly. A link
        # that opened the whole population would contradict the number above it.
        url="",
    )


def matters_by_tag(context: ReportingContext) -> MetricResult:
    """Confirmed tags only.

    Every ``TagAssignment`` row is a confirmed one by construction: a machine
    suggestion is not written until a person accepts it. The metric says so
    rather than adding a filter that would silently become wrong if that model
    rule ever changed (master specification 11.2, 21.2).
    """
    spec = definition(keys.MATTERS_BY_TAG)
    population = population_for(context, spec)
    population_count = count(population)

    tagged = count(population.filter(tag_assignments__isnull=False))
    rows = (
        population.filter(tag_assignments__isnull=False)
        .values("tags__key", "tags__name_et")
        .annotate(total=grouped_count())
        .order_by("-total", "tags__name_et")
    )
    segments = list(
        top_segments(
            [
                Segment(
                    label=row["tags__name_et"],
                    value=row["total"],
                    url=register_url(context, silt=row["tags__key"]),
                )
                for row in rows
            ]
        )
    )

    return simple_result(
        spec,
        context=context,
        value=tagged,
        population_count=population_count,
        coverage_count=tagged,
        coverage_denominator=population_count,
        segments=tuple(segments),
        # No metric-level link: "Matters carrying at least one of these" is not
        # a filter the register has, and every bar below links exactly. A link
        # that opened the whole population would contradict the number above it.
        url="",
    )


# ---------------------------------------------------------------------------
# Historical source coverage
# ---------------------------------------------------------------------------


def matters_with_historical_source(context: ReportingContext) -> MetricResult:
    spec = definition(keys.MATTERS_WITH_HISTORICAL_SOURCE)
    population = population_for(context, spec)
    population_count = count(population)
    with_source = count(population.annotate(has=_has_source_page()).filter(has=True))
    return simple_result(
        spec,
        context=context,
        value=with_source,
        population_count=population_count,
        coverage_count=with_source,
        coverage_denominator=population_count,
        url=corpus_url(context, allikas="on"),
    )


def matters_without_historical_source(context: ReportingContext) -> MetricResult:
    spec = definition(keys.MATTERS_WITHOUT_HISTORICAL_SOURCE)
    population = population_for(context, spec)
    population_count = count(population)
    without = count(population.annotate(has=_has_source_page()).filter(has=False))
    return simple_result(
        spec,
        context=context,
        value=without,
        population_count=population_count,
        url=corpus_url(context, allikas="puudub"),
        notes=(
            "Vanal registrireal ei pruukinud kunagi OneNote'i lehte olla. "
            "See ei ole andmekvaliteedi viga.",
        ),
    )


def onenote_only_matters(context: ReportingContext) -> MetricResult:
    spec = definition(keys.ONENOTE_ONLY_MATTERS)
    population = population_for(context, spec)
    total = count(population)
    return simple_result(
        spec,
        context=context,
        value=total,
        url=corpus_url(context, paritolu=MatterOrigin.LEGACY_ONENOTE.value),
        notes=(
            "Neil teemadel ei ole viitenumbrit ega registri aruandlusaastat, "
            "sest registris neid kunagi ei olnud.",
        ),
    )


def matters_with_multiple_source_pages(context: ReportingContext) -> MetricResult:
    spec = definition(keys.MATTERS_WITH_MULTIPLE_SOURCE_PAGES)
    population = population_for(context, spec)
    population_count = count(population)
    several = (
        population.annotate(page_count=Count("source_pages", distinct=True))
        .filter(page_count__gte=2)
        .distinct()
        .count()
    )
    return simple_result(
        spec,
        context=context,
        value=several,
        population_count=population_count,
        url=corpus_url(context, allikas="mitu"),
    )


def historical_source_coverage_classes(context: ReportingContext) -> MetricResult:
    """The four ways a Matter relates to the historical corpus.

    Kept as four named buckets rather than a yes/no, because "register row with
    no OneNote page" and "OneNote page with no register row" are opposite facts
    and a single coverage percentage would hide both (brief 18).
    """
    spec = definition(keys.HISTORICAL_SOURCE_COVERAGE_CLASSES)
    population = population_for(context, spec).annotate(has=_has_source_page())
    register_origins = (MatterOrigin.LEGACY_IMPORT.value, MatterOrigin.PROMOTED_LEGACY.value)
    register_parameter = ",".join(register_origins)

    buckets = [
        (
            "Registririda koos OneNote'i allikaga",
            population.filter(origin__in=register_origins, has=True),
            corpus_url(context, paritolu=register_parameter, allikas="on"),
        ),
        (
            "Registririda ilma OneNote'i allikata",
            population.filter(origin__in=register_origins, has=False),
            corpus_url(context, paritolu=register_parameter, allikas="puudub"),
        ),
        (
            "Ainult OneNote'i-põhine teema",
            population.filter(origin=MatterOrigin.LEGACY_ONENOTE),
            corpus_url(context, paritolu=MatterOrigin.LEGACY_ONENOTE.value),
        ),
        (
            "Süsteemis loodud teema",
            population.filter(origin=MatterOrigin.NATIVE),
            corpus_url(context, paritolu=MatterOrigin.NATIVE.value),
        ),
    ]
    segments = tuple(
        Segment(label=label, value=count(queryset), url=url) for label, queryset, url in buckets
    )

    return simple_result(
        spec,
        context=context,
        value=count(population),
        segments=segments,
        url=corpus_url(context),
    )
