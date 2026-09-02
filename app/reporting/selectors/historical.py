"""Ajalooline materjal — how much institutional memory there is, and its shape.

Authorization reaches these tables through the Matter, exactly like every other
read: a page is visible because a Matter somebody may see claims it. A 2019 file
about a member's insolvency is the same kind of material as this year's, and is
no less confidential for being old (Stage-2D brief 60).

Three counting rules that are easy to get wrong and expensive to get wrong.

**An occurrence is not a file.** ``LegacySourceResource`` is one file *as it sits
on one page*. The same bytes attached to two different pages are two
occurrences, and stay two, because the corpus really does contain the thing
twice. ``HISTORICAL_UNIQUE_BINARY_CONTENTS`` counts distinct SHA-256 values and
is a different number on purpose. The two are never presented as one
(brief 29).

**A page shared between Matters is still one page.** The corpus has 138 pages
that several register rows point at. They are imported once, and counted once.

**Materialisation has four states, and only one of them is a problem.** The
first real import found six attachments that are zero bytes *in OneNote
itself*; ``add_evidence_version`` refuses to store an empty evidence file, and
the page then showed them as "copying" for ever. So: imported, still to copy,
empty in the source, and copy failed. An empty original is a fact about the
lawyer's own notebook, not an importer defect, and labelling it as one would
send an operator looking for a bug that does not exist (main, commit 3888afd).

The state of one occurrence is the best state any visible link achieved for it.
A page claimed by two Matters can have its file materialised for one and not yet
for the other; the corpus-level question is whether the file has been brought
across at all, and that is what this reports.
"""

from __future__ import annotations

import functools
import operator
from typing import Any
from urllib.parse import urlencode

from django.db.models import Exists, OuterRef, Q, QuerySet, Sum
from django.urls import reverse

from app.legacy_import.source_pages import (
    LegacySourcePage,
    LegacySourceResource,
    LegacySourceResourceImport,
    MatterSourcePage,
    SourcePageRole,
)
from app.reporting import metric_catalogue as keys
from app.reporting.context import ReportingContext
from app.reporting.metric_catalogue import definition
from app.reporting.metric_types import MetricResult, Segment, distribution_from
from app.reporting.selectors.base import (
    corpus_url,
    grouped_count,
    grouped_count_over,
    simple_result,
    top_segments,
    visible_matters,
)

#: File types the corpus actually contains, in the order a reader expects them.
#: Extensions rather than MIME types: the archive preserves original filenames
#: and never claimed to know a content type it did not read.
FILE_TYPE_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "PDF": (".pdf",),
    "DOCX": (".docx",),
    "DOC": (".doc",),
    "XLSX": (".xlsx", ".xlsm"),
    "XLS": (".xls",),
    "PPTX": (".pptx",),
    "PPT": (".ppt",),
    "MSG": (".msg",),
    "EML": (".eml",),
    "ASICE": (".asice", ".asics", ".sce"),
    "BDOC": (".bdoc", ".ddoc"),
    "PILT": (".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".bmp"),
}

#: Everything whose extension is not in the table above. A real, clickable
#: bucket: a corpus is never entirely made of formats somebody listed.
OTHER_TYPE = "MUU"

#: ASiC-E and BDoc are valid historical material that nothing will ever open.
#: Kept as a named group because "not parsed" and "failed to parse" are
#: different facts and only one of them is a problem (brief 31).
SIGNED_CONTAINER_TYPES = ("ASICE", "BDOC")
EMAIL_TYPES = ("MSG", "EML")

MATERIALISATION_LABELS: dict[str, str] = {
    "imported": "Imporditud",
    "pending": "Kopeerimist ootab",
    "empty": "Allikas tühi",
    "unavailable": "Kopeerimine ebaõnnestus",
}


def _extension_q(type_key: str) -> Q:
    extensions = FILE_TYPE_EXTENSIONS[type_key]
    return functools.reduce(
        operator.or_,
        (Q(original_filename__iendswith=extension) for extension in extensions),
    )


def _any_known_extension_q() -> Q:
    return functools.reduce(operator.or_, (_extension_q(key) for key in FILE_TYPE_EXTENSIONS))


def file_type_q(type_key: str) -> Q:
    """The filter behind one file-type bar, reused by its drill-through list."""
    if type_key == OTHER_TYPE:
        return ~_any_known_extension_q()
    return _extension_q(type_key)


# ---------------------------------------------------------------------------
# Authorized populations
# ---------------------------------------------------------------------------


def visible_links(context: ReportingContext) -> QuerySet[MatterSourcePage]:
    return MatterSourcePage.objects.filter(matter__in=visible_matters(context))


def visible_pages(context: ReportingContext) -> QuerySet[LegacySourcePage]:
    """Pages claimed by at least one Matter this viewer may read.

    Resolved through a primary-key subquery rather than a joined
    ``distinct()``. The join to ``matter_links`` fans out — a page claimed by
    three Matters is three rows — and ``distinct()`` only collapses that while
    every column is still selected. The moment something narrows the selection,
    as ``values_list("text_characters")`` does for a distribution, DISTINCT
    starts deduplicating *values* instead of rows and four pages with two
    identical lengths silently become three.

    A pk subquery has no fan-out to collapse, so counts, sums and value lists
    are all exact without anyone having to remember why.
    """
    matched = LegacySourcePage.objects.filter(matter_links__matter__in=visible_matters(context))
    pages = LegacySourcePage.objects.filter(pk__in=matched.values("pk"))
    # The section filter belongs here rather than on each caller: it is one of
    # the tab's own dimensions, and applying it in one place is what keeps the
    # page count, the file count and the list on this tab talking about the
    # same corpus.
    return pages.filter(source_section=context.section) if context.section else pages


def visible_resources(
    context: ReportingContext, *, file_type: str = "", state: str = ""
) -> QuerySet[LegacySourceResource]:
    """The one population behind every material number and its list.

    The section narrowing arrives through ``visible_pages`` — it is a context
    dimension, not an argument — so a caller cannot apply it to the list and
    forget it on the count.
    """
    queryset = LegacySourceResource.objects.filter(source_page__in=visible_pages(context))

    if file_type:
        queryset = queryset.filter(file_type_q(file_type))
    if state:
        queryset = queryset.filter(materialisation_q(context, state))
    return queryset


def _import_rows(context: ReportingContext) -> QuerySet[LegacySourceResourceImport]:
    """Materialisation records reachable through a link this viewer may read."""
    return LegacySourceResourceImport.objects.filter(matter_source_page__in=visible_links(context))


def materialisation_q(context: ReportingContext, state: str) -> Q:
    """One of the four states, as a condition on ``LegacySourceResource``.

    The Python equivalent of this lives in
    ``app.legacy_import.historical_views._file_state``, which is what the case
    file page renders from. Two expressions of one rule is a smell that earns
    itself here — a page renders one record at a time and a statistic counts
    thousands — and the test suite asserts the two agree on every branch, so
    they cannot drift (the same arrangement main uses for extraction
    eligibility).
    """
    imported = _import_rows(context).filter(resource=OuterRef("pk"), document__isnull=False)
    attempted = _import_rows(context).filter(resource=OuterRef("pk"))
    unmaterialised = _import_rows(context).filter(resource=OuterRef("pk"), document__isnull=True)

    if state == "imported":
        return Q(Exists(imported))
    if state == "pending":
        return ~Q(Exists(attempted))
    if state == "empty":
        return ~Q(Exists(imported)) & Q(Exists(unmaterialised)) & Q(size_bytes=0)
    if state == "unavailable":
        return ~Q(Exists(imported)) & Q(Exists(unmaterialised)) & Q(size_bytes__gt=0)
    raise ValueError(f"Unknown materialisation state {state!r}.")


def _values(queryset: QuerySet[Any], field: str) -> list[int]:
    """One number per row, with the row's identity kept alongside.

    Selecting the primary key as well is not decoration: an authorized queryset
    can carry ``distinct()``, and a single-column ``values_list`` under DISTINCT
    deduplicates *values* — four pages of which two are empty become three. The
    pk makes every tuple unique, so the list has one entry per row.
    """
    return [row[1] for row in queryset.values_list("pk", field)]


def _section_url(context: ReportingContext, section: str) -> str:
    """The Ajalooline materjal tab itself, narrowed to one OneNote section."""
    params = {**context.query_params(), "sektsioon": section}
    return f"{reverse('reporting:historical')}?{urlencode(params)}"


def _materials_url(context: ReportingContext, **extra: str) -> str:
    """The material list, carrying every filter that is currently active.

    Starting from ``query_params`` rather than from the period alone: a reader
    who has narrowed the tab to one owner and clicks the MSG bar expects MSG
    files *for that owner*, and a link that silently widened the population
    would show a bigger number than the bar it came from.
    """
    params = {**context.query_params(), **{k: v for k, v in extra.items() if v}}
    return f"{reverse('reporting:materials')}?{urlencode(params)}"


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def legacy_source_pages(context: ReportingContext) -> MetricResult:
    spec = definition(keys.LEGACY_SOURCE_PAGES)
    pages = visible_pages(context)
    return simple_result(
        spec,
        context=context,
        value=pages.count(),
        # Pages have no list of their own — the materials list counts file
        # occurrences, which is a different number. A link to it would open
        # something other than what this card counted (Stage-2E brief 38).
        url="",
        notes=(
            "Leht imporditakse üks kord, olenemata sellest, mitu teemat sellele viitab.",
            "Lehte loetakse teema juurest; eraldi lehtede loendit ei ole.",
        ),
    )


def legacy_source_pages_by_section(context: ReportingContext) -> MetricResult:
    """The OneNote filing structure, labelled as history rather than taxonomy.

    "Maksud ja toll" is where somebody filed a document in 2019. It is not a
    ``PolicyArea``, it is not mapped to one, and presenting it as one would
    destroy the only record of how the department actually organised itself
    (Stage-2D brief 9, Stage-2E brief 21).
    """
    spec = definition(keys.LEGACY_SOURCE_PAGES_BY_SECTION)
    pages = visible_pages(context)
    rows = pages.values("source_section").annotate(total=grouped_count()).order_by("-total")
    # A section bar re-scopes the whole tab rather than opening the file list:
    # the bar counts *pages* and the file list counts *occurrences*, so a link
    # there would open a longer list than the number promised. Narrowing the
    # tab makes every number on it — pages, files, bytes, states — describe
    # that section (Stage-2E brief 38, 66).
    segments = top_segments(
        [
            Segment(
                label=row["source_section"] or "Sektsioonita",
                value=row["total"],
                url=_section_url(context, row["source_section"]),
                is_unknown=not row["source_section"],
            )
            for row in rows
        ],
    )
    return simple_result(
        spec,
        context=context,
        value=pages.count(),
        segments=segments,
        url=_materials_url(context),
        notes=("See on ajalooline lähteklassifikatsioon, mitte kanooniline valdkond.",),
    )


def legacy_source_pages_by_year(context: ReportingContext) -> MetricResult:
    """The source's own clock, kept well away from the Matter year axis."""
    spec = definition(keys.LEGACY_SOURCE_PAGES_BY_YEAR)
    pages = visible_pages(context)
    total = pages.count()
    dated = pages.filter(source_created_at__isnull=False)

    rows = (
        dated.values("source_created_at__year")
        .annotate(total=grouped_count())
        .order_by("source_created_at__year")
    )
    segments = tuple(
        Segment(label=str(row["source_created_at__year"]), value=row["total"]) for row in rows
    )
    covered = sum(segment.value for segment in segments)

    return simple_result(
        spec,
        context=context,
        value=covered,
        population_count=total,
        eligible_count=covered,
        coverage_count=covered,
        coverage_denominator=total,
        segments=segments,
        url=_materials_url(context),
        notes=("Lehe loomise ajatempel allikas. Ei ole teema aruandlusaasta.",),
    )


def legacy_source_pages_by_role(context: ReportingContext) -> MetricResult:
    spec = definition(keys.LEGACY_SOURCE_PAGES_BY_ROLE)
    pages = visible_pages(context)
    labels = dict(SourcePageRole.choices)
    rows = pages.values("page_role").annotate(total=grouped_count()).order_by("-total")
    segments = tuple(
        Segment(label=labels.get(row["page_role"], row["page_role"]), value=row["total"])
        for row in rows
    )
    return simple_result(
        spec,
        context=context,
        value=pages.count(),
        segments=segments,
        url=_materials_url(context),
    )


def reading_order_ambiguous(context: ReportingContext) -> MetricResult:
    spec = definition(keys.READING_ORDER_AMBIGUOUS)
    pages = visible_pages(context)
    ambiguous = pages.filter(reading_order_ambiguous=True)
    return simple_result(
        spec,
        context=context,
        value=ambiguous.count(),
        population_count=pages.count(),
        url=_materials_url(context),
        notes=(
            "OneNote on vaba paigutusega. Nendel lehtedel võib narratiivi "
            "järjekord olla ebatäpne — sisu ise on olemas.",
        ),
    )


def source_page_text_length(context: ReportingContext) -> MetricResult:
    spec = definition(keys.SOURCE_PAGE_TEXT_LENGTH)
    values = _values(visible_pages(context), "text_characters")
    distribution = distribution_from(values)
    return simple_result(
        spec,
        context=context,
        value=distribution.n,
        population_count=distribution.n,
        distribution=distribution,
        url=_materials_url(context),
    )


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


def historical_resource_occurrences(context: ReportingContext) -> MetricResult:
    spec = definition(keys.HISTORICAL_RESOURCE_OCCURRENCES)
    resources = visible_resources(context)
    return simple_result(
        spec,
        context=context,
        value=context.shared("historical.resource_occurrences", resources.count),
        url=_materials_url(context),
        notes=("Esinemine lehel. Sama fail kahel lehel on kaks esinemist.",),
    )


def historical_unique_binary_contents(context: ReportingContext) -> MetricResult:
    spec = definition(keys.HISTORICAL_UNIQUE_BINARY_CONTENTS)
    resources = visible_resources(context)
    unique = context.shared(
        "historical.unique_binary_contents",
        lambda: resources.values("sha256").distinct().count(),
    )
    occurrences = context.shared("historical.resource_occurrences", resources.count)
    return simple_result(
        spec,
        context=context,
        value=unique,
        population_count=occurrences,
        # The materials list enumerates occurrences, not distinct contents, so
        # it would open a longer list than this number. Deliberately unlinked.
        url="",
        notes=(
            f"{occurrences - unique} esinemist on baiditäpsed kordused juba loetud failisisust."
            if occurrences > unique
            else "Kordusi ei ole.",
        ),
    )


def historical_resource_bytes(context: ReportingContext) -> MetricResult:
    spec = definition(keys.HISTORICAL_RESOURCE_BYTES)
    resources = visible_resources(context)
    total = resources.aggregate(total=Sum("size_bytes"))["total"] or 0
    return simple_result(
        spec,
        context=context,
        value=int(total),
        population_count=context.shared("historical.resource_occurrences", resources.count),
        url=_materials_url(context),
    )


def historical_resources_by_type(context: ReportingContext) -> MetricResult:
    """Count and bytes per file type, with the tail kept as a real bucket."""
    spec = definition(keys.HISTORICAL_RESOURCES_BY_TYPE)
    resources = visible_resources(context)

    segments: list[Segment] = []
    for type_key in (*FILE_TYPE_EXTENSIONS, OTHER_TYPE):
        subset = resources.filter(file_type_q(type_key))
        number = subset.count()
        if not number:
            continue
        size = subset.aggregate(total=Sum("size_bytes"))["total"] or 0
        segments.append(
            Segment(
                label=type_key,
                value=number,
                url=_materials_url(context, failityyp=type_key),
                note=f"{int(size)} baiti",
            )
        )

    segments.sort(key=lambda segment: segment.value, reverse=True)
    return simple_result(
        spec,
        context=context,
        value=resources.count(),
        segments=tuple(segments),
        url=_materials_url(context),
    )


def _type_group(context: ReportingContext, key: str, types: tuple[str, ...]) -> MetricResult:
    spec = definition(key)
    resources = visible_resources(context)
    condition = functools.reduce(operator.or_, (file_type_q(name) for name in types))
    subset = resources.filter(condition)
    return simple_result(
        spec,
        context=context,
        value=subset.count(),
        population_count=resources.count(),
        segments=tuple(
            Segment(
                label=name,
                value=resources.filter(file_type_q(name)).count(),
                url=_materials_url(context, failityyp=name),
            )
            for name in types
        ),
        # The group's own bars link exactly; the group total does not, because
        # no single filter selects "MSG or EML".
        url="",
    )


def historical_email_resources(context: ReportingContext) -> MetricResult:
    return _type_group(context, keys.HISTORICAL_EMAIL_RESOURCES, EMAIL_TYPES)


def historical_signed_containers(context: ReportingContext) -> MetricResult:
    """ASiC-E and BDoc. Never an extraction failure.

    These are valid historical materials that are intentionally not opened, so
    ``NOT_APPLICABLE`` is the successful state for them. Counting them among
    parse failures would put a four-figure number in front of an operator and
    send them looking for a defect that is a deliberate decision (brief 31).
    """
    result = _type_group(context, keys.HISTORICAL_SIGNED_CONTAINERS, SIGNED_CONTAINER_TYPES)
    return result.with_note(
        "Neid ei avata teadlikult. Nad ei kuulu kunagi eraldamise ebaõnnestumiste hulka."
    )


def resources_per_page(context: ReportingContext) -> MetricResult:
    spec = definition(keys.RESOURCES_PER_PAGE)
    values = _values(
        visible_pages(context).annotate(files=grouped_count_over("resources")), "files"
    )
    distribution = distribution_from(values)
    return simple_result(
        spec,
        context=context,
        value=distribution.total,
        population_count=distribution.n,
        distribution=distribution,
        url=_materials_url(context),
    )


def resources_per_matter(context: ReportingContext) -> MetricResult:
    spec = definition(keys.RESOURCES_PER_MATTER)
    values = _values(
        visible_matters(context)
        .filter(source_pages__isnull=False)
        .annotate(files=grouped_count_over("source_pages__source_page__resources")),
        "files",
    )
    distribution = distribution_from(values)
    return simple_result(
        spec,
        context=context,
        value=distribution.total,
        population_count=distribution.n,
        distribution=distribution,
        url=corpus_url(context, allikas="on"),
    )


def materialisation_status(context: ReportingContext) -> MetricResult:
    """The four states, counted over occurrences, adding up to the total."""
    spec = definition(keys.MATERIALISATION_STATUS)
    resources = visible_resources(context)
    total = resources.count()

    segments = tuple(
        Segment(
            label=MATERIALISATION_LABELS[state],
            value=resources.filter(materialisation_q(context, state)).count(),
            url=_materials_url(context, seisund=state),
            note=(
                "Originaal on OneNote'is tühi — see on allika fakt, mitte impordi viga."
                if state == "empty"
                else ""
            ),
        )
        for state in ("imported", "pending", "empty", "unavailable")
    )

    return simple_result(
        spec,
        context=context,
        value=total,
        population_count=total,
        segments=segments,
        url=_materials_url(context),
    )


def materialisation_failed(context: ReportingContext) -> MetricResult:
    spec = definition(keys.MATERIALISATION_FAILED)
    resources = visible_resources(context)
    failed = resources.filter(materialisation_q(context, "unavailable")).count()
    return simple_result(
        spec,
        context=context,
        value=failed,
        population_count=resources.count(),
        url=_materials_url(context, seisund="unavailable"),
        notes=("Allikas tühjad failid ei ole siin. Need on eraldi seisund.",),
    )


def list_rows(
    context: ReportingContext, *, file_type: str = "", state: str = ""
) -> QuerySet[LegacySourceResource]:
    """The drill-through list, from the same selector the numbers used."""
    return (
        visible_resources(context, file_type=file_type, state=state)
        .select_related("source_page")
        .order_by("source_page__source_section", "source_page__page_order", "original_filename")
    )
