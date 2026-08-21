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
    grouped_count,
    grouped_count_over,
    register_url,
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
    """Pages claimed by at least one Matter this viewer may read."""
    return LegacySourcePage.objects.filter(
        matter_links__matter__in=visible_matters(context)
    ).distinct()


def visible_resources(
    context: ReportingContext, *, file_type: str = "", section: str = "", state: str = ""
) -> QuerySet[LegacySourceResource]:
    """The one population behind every material number and its list."""
    queryset = LegacySourceResource.objects.filter(
        source_page__matter_links__matter__in=visible_matters(context)
    ).distinct()

    if section:
        queryset = queryset.filter(source_page__source_section=section)
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


def _materials_url(context: ReportingContext, **extra: str) -> str:
    params = {key: value for key, value in extra.items() if value}
    params.setdefault("periood", context.period.key)
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
        url=register_url(context, allikas="on"),
        notes=("Leht imporditakse üks kord, olenemata sellest, mitu teemat sellele viitab.",),
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
    segments = top_segments(
        [
            Segment(
                label=row["source_section"] or "Sektsioonita",
                value=row["total"],
                url=_materials_url(context, sektsioon=row["source_section"]),
                is_unknown=not row["source_section"],
            )
            for row in rows
        ],
        remainder_url=_materials_url(context),
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
    values = list(visible_pages(context).values_list("text_characters", flat=True))
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
        value=resources.count(),
        url=_materials_url(context),
        notes=("Esinemine lehel. Sama fail kahel lehel on kaks esinemist.",),
    )


def historical_unique_binary_contents(context: ReportingContext) -> MetricResult:
    spec = definition(keys.HISTORICAL_UNIQUE_BINARY_CONTENTS)
    resources = visible_resources(context)
    unique = resources.values("sha256").distinct().count()
    occurrences = resources.count()
    return simple_result(
        spec,
        context=context,
        value=unique,
        population_count=occurrences,
        url=_materials_url(context),
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
        population_count=resources.count(),
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
        url=_materials_url(context, failityyp=types[0]),
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
    values = list(
        visible_pages(context)
        .annotate(files=grouped_count_over("resources"))
        .values_list("files", flat=True)
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
    values = list(
        visible_matters(context)
        .filter(source_pages__isnull=False)
        .annotate(files=grouped_count_over("source_pages__source_page__resources"))
        .values_list("files", flat=True)
    )
    distribution = distribution_from(values)
    return simple_result(
        spec,
        context=context,
        value=distribution.total,
        population_count=distribution.n,
        distribution=distribution,
        url=register_url(context, allikas="on"),
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
    context: ReportingContext, *, file_type: str = "", section: str = "", state: str = ""
) -> QuerySet[LegacySourceResource]:
    """The drill-through list, from the same selector the numbers used."""
    return (
        visible_resources(context, file_type=file_type, section=section, state=state)
        .select_related("source_page")
        .order_by("source_page__source_section", "source_page__page_order", "original_filename")
    )
