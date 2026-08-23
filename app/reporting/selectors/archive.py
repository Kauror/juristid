"""Arvamuste arhiiv as a *trend*, and the four honesty rules it depends on.

Until now the archive was inventory: how many files exist, how many of them
reached a Matter. This module turns it into history — how many letters carry
each year's date, how that year is running against the last, and which lawyers'
Matters they are tied to — without letting any of it drift into a claim the
data model does not make.

**Archive evidence is not a canonical Submission.** A `Submission` asserts that
Koda sent this exact document, on this date, to these recipients. An
`OpinionArchiveItem` asserts that a file with this name sits at this path in the
archive. The first is a statement about the department's conduct; the second is
a statement about a zip file. They are reported side by side and never added
together, which is why `SUBMISSIONS_SENT` keeps its own metric even while it has
almost no history to show (brief 24, 39, 62).

**The date is the filename's date, and is labelled as such.** `filename_date` is
what the archive's own naming convention says; the model's own comment calls it
a matching signal and not a sent date, and the register's `VÄLJA` agrees with it
on the same day in 326 cases and the next day in 227. So every label here reads
*arhiivi dokumendid kuupäeva järgi* and never *väljasaadetud* (brief 26).

**The entity is the distinct binary.** The corpus this was measured against
happens to hold 767 occurrences and 767 distinct hashes, so the two numbers are
equal today. They will not stay equal the moment a later snapshot files the same
letter twice, and a trend that counted occurrences would then report a filing
habit as advocacy volume. Occurrence inventory stays a separate coverage metric
(brief 27).

**Absence is not zero.** The archive begins in 2020; it has nothing to say about
2011–2019 and does not draw those years. Before the catalogue has been run at
all it has nothing to say about any year, and it declines rather than reporting
that Koda sent no opinions (brief 28, 29).

Authorization: anything that names or derives from a Matter is scoped through
``visible_matters`` before it is grouped, so a restricted Matter cannot move a
responsibility total or a link-coverage percentage. Unlinked archive inventory
names no Matter — a filename, a size and a hash — and follows the existing
archive reporting rule of being counted for everyone (brief 45, 78).
"""

from __future__ import annotations

from datetime import date

from django.db.models import QuerySet

from app.legacy_import.opinion_archive import OpinionArchiveItem
from app.legacy_import.opinion_binary import OpinionArchiveMatterLink
from app.matters.models import Matter
from app.reporting import metric_catalogue as keys
from app.reporting.context import ReportingContext
from app.reporting.metric_catalogue import OPINION_ARCHIVE_FIRST_YEAR, definition
from app.reporting.metric_types import (
    Comparison,
    MetricResult,
    MetricStatus,
    Segment,
)
from app.reporting.selectors import responsibility
from app.reporting.selectors.base import simple_result, visible_matters
from app.reporting.selectors.portfolio import month_label, same_day_last_year

#: What the archive metrics are counting, in the words used on every card.
DISTINCT_NOTE = (
    "Loetakse erinevaid faile (SHA-256). Sama kiri kahes kohas on üks fail ja "
    "kaks esinemist; esinemiste inventuur on eraldi näitaja."
)
DATE_BASIS_NOTE = (
    "Kuupäev on arhiivi failinime kuupäev — allika metaandmed, mitte "
    "väljasaatmise aeg. Kanoonilise saatmise fakti kannab ainult arvamuse kirje."
)
NOT_A_SUBMISSION_NOTE = (
    "Arhiivi tõendus ei ole kanooniline saadetud arvamus. Neid näitajaid ei liideta."
)
EMPTY_NOTE = (
    "Arvamuste arhiivi ei ole veel kataloogitud. See ei tähenda, et arvamusi "
    "ei saadetud — mõõtmist lihtsalt ei ole."
)
MATTER_FILTER_NOTE = "Teemafiltrid ei kitsenda seda näitajat: arhiivikirjel endal ei ole teemat."


# ---------------------------------------------------------------------------
# The corpus, and what it can speak for
# ---------------------------------------------------------------------------


def dated_items() -> QuerySet[OpinionArchiveItem]:
    """Archive occurrences carrying a readable filename date.

    Occurrences whose name did not parse are not counted on a time axis and are
    not silently treated as undated zeros either: they stay in the inventory
    metric, and the coverage line beneath a trend says how many there are.
    """
    return OpinionArchiveItem.objects.filter(filename_date__isnull=False)


def coverage_cutoff() -> date | None:
    """The latest archive date there is any evidence for.

    Everything a current-year comparison does depends on this. If the newest
    letter in the archive is dated in July, then the archive's 2026 is seven
    months long, and measuring it against a full 2025 would manufacture a
    collapse. ``None`` when nothing has been catalogued (brief 32).
    """
    return dated_items().order_by("-filename_date").values_list("filename_date", flat=True).first()


def cutoff_note(cutoff: date | None) -> str:
    if cutoff is None:
        return EMPTY_NOTE
    return f"Arhiivis on andmeid kuni {cutoff.strftime('%d.%m.%Y')}."


def _distinct_dates() -> list[tuple[str, date]]:
    """One date per distinct binary: the earliest occurrence of those bytes.

    The same letter filed twice under two names could carry two dates. Taking
    the earliest is deterministic and is the archive's first evidence of the
    document; taking whichever row the database returned first would make the
    chart depend on query planning.

    Materialised in Python rather than as a grouped query, because the value
    that reaches the axis has to be a Python ``date`` either way and the corpus
    is a few hundred rows, not a few million.
    """
    earliest: dict[str, date] = {}
    for sha, day in dated_items().order_by().values_list("sha256", "filename_date"):
        # `dated_items` already excludes null dates; the annotation is nullable
        # because the column is, so the guard is here to keep that true rather
        # than to handle a case the queryset can return.
        if day is None:
            continue
        current = earliest.get(sha)
        if current is None or day < current:
            earliest[sha] = day
    return sorted(earliest.items(), key=lambda pair: pair[1])


def _population_counts() -> tuple[int, int]:
    """(distinct binaries in the archive, distinct binaries carrying a date)."""
    total = OpinionArchiveItem.objects.values("sha256").distinct().count()
    dated = dated_items().values("sha256").distinct().count()
    return total, dated


def _empty_result(spec_key: str, context: ReportingContext) -> MetricResult:
    """How an archive metric declines before the catalogue has been run.

    ``INSUFFICIENT_DATA`` rather than a zero, because a chart reading "0" over a
    year axis is a confident historical claim that Koda sent nothing, and it
    would be wrong in exactly the situation this branch ships in: before P3
    populates anything (brief 28).
    """
    return simple_result(
        definition(spec_key),
        context=context,
        value=0,
        population_count=0,
        status=MetricStatus.INSUFFICIENT_DATA,
        notes=(EMPTY_NOTE, NOT_A_SUBMISSION_NOTE),
    )


# ---------------------------------------------------------------------------
# Trends
# ---------------------------------------------------------------------------


def opinion_archive_by_year(context: ReportingContext) -> MetricResult:
    """Distinct archive letters per year of their own filename date.

    No bar before the archive's first year, and no bar for a year the corpus
    holds nothing from: 2011–2019 are not zeros, they are years the archive was
    never asked about (brief 29, 43).
    """
    spec = definition(keys.OPINION_ARCHIVE_BY_YEAR)
    pairs = _distinct_dates()
    if not pairs:
        return _empty_result(keys.OPINION_ARCHIVE_BY_YEAR, context)

    total, dated = _population_counts()
    counted: dict[int, int] = {}
    for _sha, day in pairs:
        if day.year >= OPINION_ARCHIVE_FIRST_YEAR:
            counted[day.year] = counted.get(day.year, 0) + 1

    years = sorted(counted)
    if not context.period.is_all:
        years = [year for year in years if context.period.contains_year(year)]

    segments = tuple(Segment(label=str(year), value=counted[year]) for year in years)
    cutoff = coverage_cutoff()
    notes = [DATE_BASIS_NOTE, DISTINCT_NOTE, cutoff_note(cutoff), MATTER_FILTER_NOTE]
    if not context.period.is_all and not years:
        notes.append("Valitud periood jääb tervikuna arhiivi mõõdetud akna alt välja.")

    return simple_result(
        spec,
        context=context,
        value=sum(segment.value for segment in segments),
        population_count=total,
        eligible_count=sum(segment.value for segment in segments),
        coverage_count=dated,
        coverage_denominator=total,
        # No link: the archive has no reader-facing list under the shared gate,
        # and a link that returned a forbidden page is worse than none.
        url="",
        segments=segments,
        notes=tuple(notes),
    )


def opinion_archive_by_month(context: ReportingContext) -> MetricResult:
    """The same letters by month, and never past the evidence.

    The axis stops at the archive's latest date rather than at December. Drawing
    the remaining months as measured zeros would report five months of silence
    that nobody measured (brief 31).
    """
    spec = definition(keys.OPINION_ARCHIVE_BY_MONTH)
    pairs = [(sha, day) for sha, day in _distinct_dates() if day.year >= OPINION_ARCHIVE_FIRST_YEAR]
    if not pairs:
        return _empty_result(keys.OPINION_ARCHIVE_BY_MONTH, context)

    if context.period.is_all:
        selected = pairs
    else:
        selected = [(sha, day) for sha, day in pairs if context.period.contains_year(day.year)]

    if not selected:
        total, dated = _population_counts()
        return simple_result(
            spec,
            context=context,
            value=0,
            population_count=total,
            coverage_count=dated,
            coverage_denominator=total,
            status=MetricStatus.INSUFFICIENT_DATA,
            notes=(
                DATE_BASIS_NOTE,
                cutoff_note(coverage_cutoff()),
                "Valitud perioodi kohta arhiivis mõõtmist ei ole.",
            ),
        )

    first = min(day for _sha, day in selected)
    last = max(day for _sha, day in selected)
    counted: dict[tuple[int, int], int] = {}
    for _sha, day in selected:
        key = (day.year, day.month)
        counted[key] = counted.get(key, 0) + 1

    single_year = first.year == last.year
    months: list[tuple[int, int]] = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    segments = tuple(
        Segment(
            label=month_label(year, month, single_year=single_year),
            value=counted.get((year, month), 0),
        )
        for year, month in months
    )

    total, dated = _population_counts()
    return simple_result(
        spec,
        context=context,
        value=sum(segment.value for segment in segments),
        population_count=total,
        eligible_count=sum(segment.value for segment in segments),
        coverage_count=dated,
        coverage_denominator=total,
        url="",
        segments=segments,
        notes=(
            DATE_BASIS_NOTE,
            DISTINCT_NOTE,
            f"Telg lõpeb viimasel mõõdetud kuupäeval: {last.strftime('%d.%m.%Y')}.",
            MATTER_FILTER_NOTE,
        ),
    )


def opinion_archive_yoy_change(context: ReportingContext) -> MetricResult:
    """The archive's latest year against the same stretch of the year before.

    Same cutoff on both sides. If the newest evidence is dated 31 July, this
    compares 1 January – 31 July of that year with 1 January – 31 July of the
    previous one, and the previous year's August–December are outside the
    comparison entirely (brief 33, 72).

    A previous side of zero yields an absolute difference and no percentage. A
    change from nothing has no percentage, and printing one — 100 %, or ∞ —
    would be read as a measurement (brief 73).
    """
    spec = definition(keys.OPINION_ARCHIVE_YOY_CHANGE)
    pairs = _distinct_dates()
    cutoff = coverage_cutoff()
    if not pairs or cutoff is None:
        return _empty_result(keys.OPINION_ARCHIVE_YOY_CHANGE, context)

    previous_cutoff = same_day_last_year(cutoff)
    current_start = date(cutoff.year, 1, 1)
    previous_start = date(cutoff.year - 1, 1, 1)

    current = sum(1 for _sha, day in pairs if current_start <= day <= cutoff)
    previous = sum(1 for _sha, day in pairs if previous_start <= day <= previous_cutoff)

    comparison = Comparison(
        current_value=current,
        previous_value=previous,
        current_period_label=(
            f"{current_start.strftime('%d.%m.%Y')} – {cutoff.strftime('%d.%m.%Y')}"
        ),
        previous_period_label=(
            f"{previous_start.strftime('%d.%m.%Y')} – {previous_cutoff.strftime('%d.%m.%Y')}"
        ),
        coverage_cutoff=cutoff,
        cutoff_note=(
            "Mõlemad perioodid on lõigatud arhiivi viimasel kuupäeval, et osalist "
            "aastat ei võrreldaks terve aastaga."
        ),
    )

    total, _dated = _population_counts()
    return simple_result(
        spec,
        context=context,
        value=current,
        population_count=total,
        eligible_count=current,
        url="",
        comparison=comparison,
        notes=(
            DATE_BASIS_NOTE,
            NOT_A_SUBMISSION_NOTE,
            "Mahu muutus, mitte tulemuslikkus. Rohkem arvamusi ei ole iseenesest parem.",
        ),
    )


# ---------------------------------------------------------------------------
# Links to Matters
# ---------------------------------------------------------------------------


def _linked_rows(context: ReportingContext) -> QuerySet[OpinionArchiveMatterLink]:
    """Archive links whose Matter this reader may open.

    Scoped before anything is grouped. A link to a restricted Matter must not
    move a responsibility total, a coverage percentage or a monthly cell, and
    filtering after counting would leave it inside every one of them (brief 45).

    Only the derived and reviewed link layer. ``OpinionMatchCandidate`` rows in
    ``PENDING`` are proposals nobody has accepted, and treating a proposal as a
    link would report the queue's optimism as coverage (brief 36).
    """
    return OpinionArchiveMatterLink.objects.filter(matter__in=visible_matters(context))


def _linked_shas(context: ReportingContext) -> set[str]:
    return set(_linked_rows(context).values_list("binary__sha256", flat=True))


def opinion_archive_link_coverage(context: ReportingContext) -> MetricResult:
    """How much of the archive has reached a Teema, as distinct files.

    A letter concerning three Matters is linked once here, not three times: the
    question is how much of the corpus is placed, and multiplying a file by its
    relationships would answer a different one (brief 74).

    *Sidumata* means the file has no link to a Matter this reader may see. It
    does not mean a missing opinion — the evidence is held and catalogued, and
    tying it to a Teema is work that has not been done yet (brief 38).
    """
    spec = definition(keys.OPINION_ARCHIVE_LINK_COVERAGE)
    total = OpinionArchiveItem.objects.values("sha256").distinct().count()
    if not total:
        return _empty_result(keys.OPINION_ARCHIVE_LINK_COVERAGE, context)

    archive_shas = set(OpinionArchiveItem.objects.values_list("sha256", flat=True))
    linked = len(archive_shas & _linked_shas(context))
    percent = round(linked / total * 100) if total else 0

    return simple_result(
        spec,
        context=context,
        value=percent,
        population_count=total,
        eligible_count=linked,
        coverage_count=linked,
        coverage_denominator=total,
        url="",
        segments=(
            Segment(label="Teemaga seotud", value=linked),
            Segment(
                label="Teemaga sidumata",
                value=total - linked,
                note="Tõendus on olemas, teemaga sidumine on tegemata",
                is_unknown=True,
            ),
        ),
        notes=(
            DISTINCT_NOTE,
            "Sidumata fail ei ole puuduv arvamus, vaid arhiivitõendus, mis ei ole "
            "veel teemaga seotud.",
            "Loetakse ainult seoseid teemadega, mida sina näed.",
        ),
    )


def opinion_archive_linked_by_responsibility(context: ReportingContext) -> MetricResult:
    """Distinct archive files per responsible lawyer, through their Matters.

    **The measure is a file, and a file may appear under more than one name.**
    Four binaries in the measured corpus genuinely concern several Matters, and
    the model has no notion of a primary one — inventing one would put a letter
    under somebody arbitrary. So a file linked to two lawyers' Matters is
    counted once under each, the segments can therefore add up to more than the
    corpus total, and the definition says so rather than leaving a reader to
    discover it by adding the bars up (brief 37, 75).
    """
    spec = definition(keys.OPINION_ARCHIVE_LINKED_BY_RESPONSIBILITY)
    total = OpinionArchiveItem.objects.values("sha256").distinct().count()
    if not total:
        return _empty_result(keys.OPINION_ARCHIVE_LINKED_BY_RESPONSIBILITY, context)

    archive_shas = set(OpinionArchiveItem.objects.values_list("sha256", flat=True))
    rows = _linked_rows(context).values_list(
        "binary__sha256",
        f"matter__{responsibility.SOURCE_PATH}",
        f"matter__{responsibility.OWNER_PATH}",
    )

    per_label: dict[str, set[str]] = {}
    for sha, source_name, owner_name in rows:
        if sha not in archive_shas:
            continue
        label = responsibility.label_for(source_name, owner_name)
        per_label.setdefault(label, set()).add(sha)

    if not per_label:
        return simple_result(
            spec,
            context=context,
            value=0,
            population_count=total,
            status=MetricStatus.INSUFFICIENT_DATA,
            notes=(
                "Ühtki arhiivifaili ei ole veel seotud teemaga, mida sina näed.",
                NOT_A_SUBMISSION_NOTE,
            ),
        )

    labels = sorted(
        per_label,
        key=lambda label: (label == responsibility.UNASSIGNED_LABEL, label),
    )
    segments = tuple(
        Segment(
            label=label,
            value=len(per_label[label]),
            url="",
            is_unknown=label == responsibility.UNASSIGNED_LABEL,
        )
        for label in labels
    )
    distinct_linked = len(set().union(*per_label.values()))

    return simple_result(
        spec,
        context=context,
        value=distinct_linked,
        population_count=total,
        eligible_count=distinct_linked,
        coverage_count=distinct_linked,
        coverage_denominator=total,
        url="",
        segments=segments,
        notes=(
            "Üks fail võib puudutada mitut teemat ja on siis arvestatud iga "
            "vastutaja juures. Seetõttu võib rühmade summa ületada erinevate "
            "failide koguarvu.",
            NOT_A_SUBMISSION_NOTE,
            "Arhiivi inventuur, mitte juristide võrdlus.",
        ),
    )


def opinion_archive_linked_by_month_and_responsibility(context: ReportingContext) -> MetricResult:
    """Linked archive files by month and by responsible lawyer.

    Same multiplicity rule as the composition: a file concerning two Matters
    appears in both lawyers' columns of its own month, and the note says so.
    Nothing here picks a primary Matter (brief 41).
    """
    spec = definition(keys.OPINION_ARCHIVE_LINKED_BY_MONTH_RESPONSIBILITY)
    total = OpinionArchiveItem.objects.values("sha256").distinct().count()
    if not total:
        return _empty_result(keys.OPINION_ARCHIVE_LINKED_BY_MONTH_RESPONSIBILITY, context)

    dates = dict(_distinct_dates())
    rows = _linked_rows(context).values_list(
        "binary__sha256",
        f"matter__{responsibility.SOURCE_PATH}",
        f"matter__{responsibility.OWNER_PATH}",
    )

    seen: set[tuple[str, str]] = set()
    counts: dict[object, dict[str, int]] = {}
    months: set[tuple[int, int]] = set()
    for sha, source_name, owner_name in rows:
        day = dates.get(sha)
        if day is None or day.year < OPINION_ARCHIVE_FIRST_YEAR:
            continue
        if not context.period.is_all and not context.period.contains_year(day.year):
            continue
        label = responsibility.label_for(source_name, owner_name)
        if (sha, label) in seen:
            # The same bytes at two archive paths are one file, so a second
            # occurrence must not add a second count under the same lawyer.
            continue
        seen.add((sha, label))
        key = (day.year, day.month)
        months.add(key)
        bucket = counts.setdefault(key, {})
        bucket[label] = bucket.get(label, 0) + 1

    if not counts:
        return simple_result(
            spec,
            context=context,
            value=0,
            population_count=total,
            status=MetricStatus.INSUFFICIENT_DATA,
            notes=(
                "Valitud perioodil ei ole ühtki arhiivifaili seotud teemaga, mida sina näed.",
                NOT_A_SUBMISSION_NOTE,
            ),
        )

    ordered = sorted(months)
    single_year = ordered[0][0] == ordered[-1][0]
    row_pairs: list[tuple[object, str]] = [
        ((year, month), month_label(year, month, single_year=single_year))
        for year, month in ordered
    ]
    matrix = responsibility.matrix(row_header="Kuu", rows=row_pairs, counts=counts)

    notes = [
        DATE_BASIS_NOTE,
        "Üks fail võib puudutada mitut teemat ja on siis arvestatud iga vastutaja "
        "juures; ridade summa võib seetõttu ületada erinevate failide arvu.",
        NOT_A_SUBMISSION_NOTE,
    ]
    if matrix.folded_note:
        notes.append(matrix.folded_note)

    return simple_result(
        spec,
        context=context,
        value=matrix.grand_total,
        population_count=total,
        eligible_count=matrix.grand_total,
        url="",
        matrix=matrix,
        notes=tuple(notes),
    )


def visible_linked_matters(context: ReportingContext) -> QuerySet[Matter]:
    """Matters this reader may see that carry at least one archive link.

    Exported for tests and for any later drill-through: the population is
    derived from the same scoped link rows the metrics group.
    """
    return visible_matters(context).filter(opinion_archive_links__isnull=False).distinct()
