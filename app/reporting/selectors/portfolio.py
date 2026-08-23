"""What the department is holding, and how new work has arrived over time.

Six answers, and each is a question the register could be asked but the first
Statistika pass could not: what stage the *currently active* files are at, who
is responsible for them, how work divided among lawyers in each year of the
register, how many genuinely new matters arrived in each month, who they went
to, and whether this year is running above or below last.

Three rules run through the module.

**Active means open and FULL.** ``MATTERS_BY_STAGE`` answers a question about a
population the period defines, and over "all years" that population is fifteen
years of imported register rows. The operational question — *what is on the
department's desk now* — needs its own metric with its own population, because
answering it from an archive-inclusive chart makes every stage count meaningless
(``selectors.base.active_full``).

**Arrival is ``received_date``, never ``created_at``.** The row for a matter
that arrived in March was written whenever somebody typed it in, and a monthly
intake chart built on the database timestamp reports the day of the import as
the busiest month in the register's history. The same reason the year axis is a
reporting year (``metric_types.TimeBasis``).

**Nothing here measures a person.** Responsibility is inventory: which files sit
under which name. Two lawyers with the same count are not doing the same amount
of work, one file can be a year of consultation and the next a two-line
monitoring note, and no ordering, colour or wording on these charts suggests
otherwise (master specification 18.8, brief 18).
"""

from __future__ import annotations

from datetime import date

from django.db.models import QuerySet

from app.matters.enums import RecordMode
from app.matters.models import Matter
from app.matters.selectors import MISSING
from app.reporting import metric_catalogue as keys
from app.reporting.context import ReportingContext
from app.reporting.metric_catalogue import definition
from app.reporting.metric_types import Comparison, MetricResult, MetricStatus, Segment
from app.reporting.selectors import responsibility
from app.reporting.selectors.base import (
    count,
    eligible_matters,
    grouped_count,
    in_reporting_year,
    known_year_q,
    population_for,
    register_url,
    simple_result,
)
from app.reporting.selectors.matters import NO_STAGE_LABEL, UNKNOWN_YEAR_LABEL

#: Estonian month abbreviations, indexed from one. A one-year view says
#: *Veebr*; a multi-year view needs the year as well, because a chart that
#: pooled every February of the register into one bar would answer a question
#: nobody asked (brief 22).
MONTH_LABELS: tuple[str, ...] = (
    "",
    "Jaan",
    "Veebr",
    "Märts",
    "Apr",
    "Mai",
    "Juuni",
    "Juuli",
    "Aug",
    "Sept",
    "Okt",
    "Nov",
    "Dets",
)


def month_label(year: int, month: int, *, single_year: bool) -> str:
    return MONTH_LABELS[month] if single_year else f"{year}-{month:02d}"


def months_between(start: date, end: date) -> list[tuple[int, int]]:
    """Every (year, month) from ``start`` to ``end`` inclusive.

    Continuous rather than only the months that carry records: inside a window
    the source demonstrably covers, a month with nothing in it *is* a
    measurement of nothing, and leaving the bar out would hide a quiet month
    rather than report it. Outside the window there is no bar at all, which is
    the opposite case and is decided by the window, not here.
    """
    months: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def same_day_last_year(day: date) -> date:
    """The same calendar day a year earlier, and 28 February for a leap day.

    A comparison cutoff has to exist in both years. Rolling 29 February forward
    to 1 March would put a day of the previous year's March into a window
    labelled February.
    """
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return date(day.year - 1, 2, 28)


# ---------------------------------------------------------------------------
# The active portfolio
# ---------------------------------------------------------------------------


def _active(context: ReportingContext, spec_key: str) -> QuerySet[Matter]:
    """Open FULL Matters this reader may see, under the metric's own eligibility."""
    return eligible_matters(context, definition(spec_key)).filter(is_open=True)


def active_full_matters_by_stage(context: ReportingContext) -> MetricResult:
    """Where the currently active files stand — not the whole register.

    ``MATTERS_BY_STAGE`` counts every visible Matter the period contains, which
    over the whole corpus is mostly archive rows that were never given a stage.
    This one answers *Mis seisus on praegu aktiivsed teemad?* and its population
    is the open FULL portfolio, so its unassigned bucket is a real gap rather
    than the archive's legitimate sparseness (brief 19).
    """
    spec = definition(keys.ACTIVE_FULL_MATTERS_BY_STAGE)
    population = _active(context, keys.ACTIVE_FULL_MATTERS_BY_STAGE)
    population_count = count(population)

    def url(**extra: str) -> str:
        return register_url(context, olek="avatud", liik=RecordMode.FULL.value, aasta="", **extra)

    rows = (
        population.filter(stage__isnull=False)
        .order_by()
        .values("stage__key", "stage__label_et", "stage__sort_order")
        .annotate(total=grouped_count())
        .order_by("stage__sort_order", "stage__label_et")
    )
    segments = [
        Segment(
            label=row["stage__label_et"], value=row["total"], url=url(hetkeseis=row["stage__key"])
        )
        for row in rows
    ]
    classified = sum(segment.value for segment in segments)

    if population_count - classified:
        segments.append(
            Segment(
                label=NO_STAGE_LABEL,
                value=population_count - classified,
                url=url(hetkeseis=MISSING),
                note="Aktiivne töö ilma menetlusetapita",
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
        url=url(),
    )


def active_full_matters_by_responsibility(context: ReportingContext) -> MetricResult:
    """Who the currently active files sit with, as the source names them.

    Point-in-time composition of the open FULL portfolio. The register's own
    ``VASTUTAJA`` text wins over the resolved account, so a colleague with no
    login here keeps their row instead of being folded into *Määramata*
    (``selectors.responsibility``, brief 20).
    """
    spec = definition(keys.ACTIVE_FULL_MATTERS_BY_RESPONSIBILITY)
    population = _active(context, keys.ACTIVE_FULL_MATTERS_BY_RESPONSIBILITY)
    population_count = count(population)
    segments = responsibility.segments(population)
    named = sum(
        segment.value for segment in segments if segment.label != responsibility.UNASSIGNED_LABEL
    )

    return simple_result(
        spec,
        context=context,
        value=population_count,
        population_count=population_count,
        coverage_count=named,
        coverage_denominator=population_count,
        segments=segments,
        url=register_url(context, olek="avatud", liik=RecordMode.FULL.value, aasta=""),
        notes=("Portfelli inventuur. Teemade arv ei mõõda tehtud tööd ega järjesta juriste.",),
    )


def matters_by_responsibility(context: ReportingContext) -> MetricResult:
    """The same dimension over the selected period rather than over now.

    Kept beside the matrix rather than folded into it: this is where every
    historical name appears in full, including the ones a wide matrix groups
    into its labelled tail column.
    """
    spec = definition(keys.MATTERS_BY_RESPONSIBILITY)
    population = population_for(context, spec)
    population_count = count(population)
    segments = responsibility.segments(population)
    named = sum(
        segment.value for segment in segments if segment.label != responsibility.UNASSIGNED_LABEL
    )

    return simple_result(
        spec,
        context=context,
        value=population_count,
        population_count=population_count,
        coverage_count=named,
        coverage_denominator=population_count,
        segments=segments,
        url=register_url(context),
        notes=("Vastutuse jaotus on inventuur, mitte töökoormus ega tulemuslikkus.",),
    )


# ---------------------------------------------------------------------------
# Year × responsibility
# ---------------------------------------------------------------------------


def matters_by_year_and_responsibility(context: ReportingContext) -> MetricResult:
    """How legislative work divided among lawyers in each year of the register.

    One grouped query, folded in Python. The alternative — a query per year or
    per person — is the shape that turns a sixteen-year table into a hundred
    round trips (brief 82).

    Years with no records are not rows. A 2013 line of zeros would say the
    department did nothing that year, when what is true is that this population
    holds nothing from it (brief 24).
    """
    spec = definition(keys.MATTERS_BY_YEAR_AND_RESPONSIBILITY)
    # ``eligible_matters`` rather than ``population_for``, for the same reason
    # ``matters_by_reporting_year`` does it: the period is applied to the *rows*
    # below, and applying it to the population as well would empty the
    # unknown-year bucket — which is precisely the count a reader needs to be
    # told about when a year is selected.
    population = eligible_matters(context, spec)
    population_count = count(population)
    with_year = count(population.filter(known_year_q()))

    dated = population.filter(known_year_q())
    if not context.period.is_all:
        dated = in_reporting_year(dated, context)

    rows = (
        dated.order_by()
        .values("reporting_year", responsibility.SOURCE_PATH, responsibility.OWNER_PATH)
        .annotate(total=grouped_count())
    )

    counts: dict[object, dict[str, int]] = {}
    for row in rows:
        label = responsibility.label_for(
            row[responsibility.SOURCE_PATH], row[responsibility.OWNER_PATH]
        )
        bucket = counts.setdefault(row["reporting_year"], {})
        bucket[label] = bucket.get(label, 0) + row["total"]

    unknown_year_counts = responsibility.tally(population.exclude(known_year_q()))
    unknown_rows: frozenset[object] = frozenset()
    if unknown_year_counts and context.period.is_all:
        counts[UNKNOWN_YEAR_LABEL] = dict(unknown_year_counts)
        unknown_rows = frozenset({UNKNOWN_YEAR_LABEL})

    ordered_keys = sorted(key for key in counts if isinstance(key, int))
    row_pairs: list[tuple[object, str]] = [(year, str(year)) for year in ordered_keys]
    if UNKNOWN_YEAR_LABEL in counts:
        row_pairs.append((UNKNOWN_YEAR_LABEL, UNKNOWN_YEAR_LABEL))

    matrix = responsibility.matrix(
        row_header="Aasta",
        rows=row_pairs,
        counts=counts,
        unknown_rows=unknown_rows,
    )

    notes = [
        "Teemade jaotus vastutajate vahel. Inventuur, mitte töökoormus, "
        "tulemuslikkus ega juristide järjestus.",
    ]
    if matrix.folded_note:
        notes.append(matrix.folded_note)
    if unknown_year_counts and not context.period.is_all:
        notes.append(
            f"{sum(unknown_year_counts.values())} teemat jääb aastatelgelt välja, "
            "sest registri aruandlusaastat ei ole."
        )

    return simple_result(
        spec,
        context=context,
        value=matrix.grand_total,
        population_count=population_count,
        eligible_count=matrix.grand_total,
        coverage_count=with_year,
        coverage_denominator=population_count,
        # No metric-level link: the register has no "year × responsible person"
        # filter, and the source name is not the column it filters on.
        url="",
        matrix=matrix,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Monthly intake
# ---------------------------------------------------------------------------


def _native_intake(context: ReportingContext, spec_key: str) -> QuerySet[Matter]:
    """Native FULL Matters carrying a real arrival date.

    Imported register rows are excluded by the definition's ``eligible_origins``
    rather than here, so the population cannot drift from what the catalogue
    says. A register row's source gives a reporting year and no month, and
    inventing one would be the fabrication the brief forbids (brief 21).
    """
    return eligible_matters(context, definition(spec_key)).filter(received_date__isnull=False)


def _intake_window(
    context: ReportingContext, population: QuerySet[Matter]
) -> tuple[date, date] | None:
    """The months an intake chart may draw, from the data and the period.

    Bounded by the measured dates rather than by the calendar: drawing to
    December of the current year would present six unmeasured months as six
    months of no new work.
    """
    first = population.order_by("received_date").values_list("received_date", flat=True).first()
    last = population.order_by("-received_date").values_list("received_date", flat=True).first()
    if first is None or last is None:
        return None

    if context.period.is_all:
        return first, last

    start = context.period.start_date
    end = context.period.end_date
    if start is None or end is None:  # pragma: no cover - is_all covers this
        return first, last
    return max(first, start), min(last, end)


def new_native_full_matters_by_month(context: ReportingContext) -> MetricResult:
    """New native work by the month it arrived.

    The same population and the same clock as ``NEW_NATIVE_FULL_MATTERS``, drawn
    as a series rather than as one number. No month segment carries a link: the
    register filters by year and there is no month filter to open, and a link to
    the whole year would open a longer list than the bar it came from
    (brief 58).
    """
    spec = definition(keys.NEW_NATIVE_FULL_MATTERS_BY_MONTH)
    population = _native_intake(context, keys.NEW_NATIVE_FULL_MATTERS_BY_MONTH)
    eligible = population if context.period.is_all else _in_period(population, context)
    window = _intake_window(context, eligible)

    if window is None:
        return simple_result(
            spec,
            context=context,
            value=0,
            population_count=count(population),
            status=MetricStatus.INSUFFICIENT_DATA,
            notes=("Valitud perioodil ei ole ühtki saabumise kuupäevaga süsteemis loodud teemat.",),
        )

    start, end = window
    months = months_between(start, end)
    single_year = start.year == end.year
    counted = {
        (row["received_date__year"], row["received_date__month"]): row["total"]
        for row in eligible.order_by()
        .values("received_date__year", "received_date__month")
        .annotate(total=grouped_count())
    }
    segments = tuple(
        Segment(
            label=month_label(year, month, single_year=single_year),
            value=counted.get((year, month), 0),
        )
        for year, month in months
    )

    return simple_result(
        spec,
        context=context,
        value=sum(segment.value for segment in segments),
        population_count=count(population),
        eligible_count=sum(segment.value for segment in segments),
        segments=segments,
        # No link, for the reason ``NEW_NATIVE_FULL_MATTERS`` carries none: the
        # register has no "has an arrival date" filter, so the list it opened
        # would be longer than the number above it.
        url="",
        notes=(
            f"Mõõdetud vahemik: {start.isoformat()} – {end.isoformat()}.",
            "Imporditud registriridadel on ainult aruandlusaasta, mitte kuu, "
            "seega neid siin ei ole.",
        ),
    )


def _in_period(queryset: QuerySet[Matter], context: ReportingContext) -> QuerySet[Matter]:
    if context.period.is_all:
        return queryset
    return queryset.filter(
        received_date__gte=context.period.start_date,
        received_date__lte=context.period.end_date,
    )


def new_native_matters_by_responsibility_month(context: ReportingContext) -> MetricResult:
    """Who received the new work of each month. Intake distribution, not output.

    One grouped query over (year, month, source name, owner name), folded in
    Python — never a query per month or per person.
    """
    spec = definition(keys.NEW_NATIVE_MATTERS_BY_RESPONSIBILITY_MONTH)
    population = _native_intake(context, keys.NEW_NATIVE_MATTERS_BY_RESPONSIBILITY_MONTH)
    eligible = _in_period(population, context)
    window = _intake_window(context, eligible)

    if window is None:
        return simple_result(
            spec,
            context=context,
            value=0,
            population_count=count(population),
            status=MetricStatus.INSUFFICIENT_DATA,
            notes=("Valitud perioodil ei ole ühtki saabumise kuupäevaga süsteemis loodud teemat.",),
        )

    start, end = window
    single_year = start.year == end.year
    rows = (
        eligible.order_by()
        .values(
            "received_date__year",
            "received_date__month",
            responsibility.SOURCE_PATH,
            responsibility.OWNER_PATH,
        )
        .annotate(total=grouped_count())
    )

    counts: dict[object, dict[str, int]] = {}
    for row in rows:
        key = (row["received_date__year"], row["received_date__month"])
        label = responsibility.label_for(
            row[responsibility.SOURCE_PATH], row[responsibility.OWNER_PATH]
        )
        bucket = counts.setdefault(key, {})
        bucket[label] = bucket.get(label, 0) + row["total"]

    row_pairs: list[tuple[object, str]] = [
        ((year, month), month_label(year, month, single_year=single_year))
        for year, month in months_between(start, end)
    ]
    matrix = responsibility.matrix(row_header="Kuu", rows=row_pairs, counts=counts)

    notes = [
        f"Mõõdetud vahemik: {start.isoformat()} – {end.isoformat()}.",
        "Uue töö jaotus saabumise järgi. Ei mõõda tehtud tööd ega järjesta juriste.",
    ]
    if matrix.folded_note:
        notes.append(matrix.folded_note)

    return simple_result(
        spec,
        context=context,
        value=matrix.grand_total,
        population_count=count(population),
        eligible_count=matrix.grand_total,
        url="",
        matrix=matrix,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Year on year
# ---------------------------------------------------------------------------


def new_native_matters_yoy_change(context: ReportingContext) -> MetricResult:
    """This year so far against the same stretch of last year.

    Same-cutoff, always. Seven months of this year measured against twelve of
    last would report a collapse every year until December, and a reader has no
    way to see the mistake from the card (brief 33, 35).

    Neither direction is good news here. More new matters arriving is more work
    the department was handed, not a result it achieved, so the wording and the
    colours stay neutral (brief 34).
    """
    spec = definition(keys.NEW_NATIVE_MATTERS_YOY_CHANGE)
    population = _native_intake(context, keys.NEW_NATIVE_MATTERS_YOY_CHANGE)

    cutoff = context.today
    previous_cutoff = same_day_last_year(cutoff)
    current_start = date(cutoff.year, 1, 1)
    previous_start = date(cutoff.year - 1, 1, 1)

    current = count(population.filter(received_date__gte=current_start, received_date__lte=cutoff))
    previous = count(
        population.filter(received_date__gte=previous_start, received_date__lte=previous_cutoff)
    )

    comparison = Comparison(
        current_value=current,
        previous_value=previous,
        current_period_label=f"{current_start.isoformat()} – {cutoff.isoformat()}",
        previous_period_label=f"{previous_start.isoformat()} – {previous_cutoff.isoformat()}",
        coverage_cutoff=cutoff,
        cutoff_note="Mõlemad perioodid on lõigatud samal kuupäeval.",
    )

    ever = count(population)
    status = MetricStatus.AVAILABLE if ever else MetricStatus.INSUFFICIENT_DATA
    return simple_result(
        spec,
        context=context,
        value=current,
        population_count=ever,
        eligible_count=current,
        status=status,
        # Same reason as the monthly trend, with one more: no register filter
        # expresses "arrived between 1 January and today".
        url="",
        notes=(
            "Mahu muutus, mitte tulemuslikkus. Rohkem saabunud teemasid ei ole "
            "iseenesest parem ega halvem.",
        ),
        comparison=comparison,
    )
