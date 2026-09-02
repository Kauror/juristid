"""The Statistika workspace: five tabs, two drill-through lists, four exports.

Every view here is ``@gate_required`` rather than ``@login_required``, and
authorizes as ``viewer_for(request)``. In shared-gate mode somebody arrives
before choosing a persona, and the department scope — NORMAL visibility, no
participation — is exactly what they may see. Reaching for ``request.user``
instead would render an empty page to a reader who is entitled to the
department's statistics, and borrowing an arbitrary person's identity to fill
it would show one lawyer's restricted files to whoever knew a shared password
(Stage-2D auth brief 6, Stage-2E brief 11).

Views do no arithmetic. They parse the URL into a ``ReportingContext``, ask
``services.compute`` for results, and render. Everything a number means is
decided in the catalogue and the selectors, which is what makes the tests able
to assert it without a browser.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse, StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone

from app.core.decorators import gate_required, viewer_for
from app.reporting import charts, exports, filters, overview_strip, services
from app.reporting import context as reporting_context
from app.reporting import metric_catalogue as metric_keys
from app.reporting.context import ReportingContext
from app.reporting.metric_catalogue import CATALOGUE, DEFERRED_METRICS, definition
from app.reporting.metric_types import MetricResult
from app.reporting.selectors import documents, historical, quality
from app.reporting.selectors import submissions as submission_selectors
from app.workflow.dates import year_from

PAGE_SIZE = 50

#: The metrics the overview strip and rail read. Existing catalogued
#: definitions, read for their value and their own drill-through — not a second
#: arithmetic taken beside them (app/reporting/overview_strip.py).
OVERVIEW_STRIP_METRICS = [
    metric_keys.ACTIVE_FULL_MATTERS,
    metric_keys.NEW_NATIVE_FULL_MATTERS,
    metric_keys.SUBMISSIONS_SENT,
]


def compute_many_by_key(keys: list[str], context: ReportingContext) -> dict[str, MetricResult]:
    """The same results ``compute_many`` returns, addressable by key.

    The strip names the figures it wants rather than depending on list order,
    which is what stops a metric added to the group above silently shifting
    every caption down one.
    """
    return {key: services.compute(key, context) for key in keys}


#: The tab strip, in reading order: what happened, about what, by whom, out of
#: what material, and where the data falls short.
#: Renamed by the v2 design, which names five tabs — Ülevaade, Teemad, Tegevus,
#: Ajalugu, Definitsioonid (02-EKRAANID §E). Andmekvaliteet is not in that list
#: and is kept: it is a working page, and a tab strip that dropped it would hide
#: it rather than retire it (docs/design-v2-compatibility.md, DS-07).
#:
#: Definitsioonid moved from a quiet button in the head onto the strip, because
#: the design names it as one of the five and because it is a destination like
#: the others rather than an action.
TABS: tuple[tuple[str, str, str], ...] = (
    ("ulevaade", "Ülevaade", "reporting:overview"),
    ("teemad", "Teemad", "reporting:matters"),
    ("tegevus", "Tegevus", "reporting:activity"),
    ("ajalooline", "Ajalugu", "reporting:historical"),
    ("andmekvaliteet", "Andmekvaliteet", "reporting:quality"),
    ("definitsioonid", "Definitsioonid", "reporting:definitions"),
)


def _shell(request: HttpRequest, context: ReportingContext, tab: str) -> dict[str, Any]:
    """Everything every Statistika page needs around its own content."""
    # Derived once and handed to both controls, so the year list and the quick
    # strip cannot disagree about which periods are on offer.
    years = filters.available_years(context)
    return {
        "reporting": context,
        "tabs": TABS,
        "active_tab": tab,
        "period_options": filters.period_options(
            context, also_offered=[year.key for year in years]
        ),
        "available_years": years,
        "chips": filters.chips(context),
        "hidden_inputs": filters.hidden_inputs(context),
        # The whole filter state, for links that change one thing and must keep
        # everything else: the tab strip, the export button, the definitions.
        "filter_query": urlencode(context.query_params()),
        "query_string": request.GET.urlencode(),
        "nav_active": "statistika",
        **filters.options(context, tab),
    }


def _bars(results: list[MetricResult]) -> list[dict[str, Any]]:
    """Pair each composition result with its geometry and its text alternative.

    Done here rather than in the template because a template that computes a
    bar width is a template that can compute it differently from the one next
    to it.
    """
    return [
        {
            "result": result,
            "bars": charts.bars(result),
            "summary": charts.summarise(result),
        }
        for result in results
    ]


def _trends(results: list[MetricResult]) -> list[dict[str, Any]]:
    return [
        {
            "result": result,
            "trend": charts.trend(result),
            "bars": charts.bars(result),
            "summary": charts.summarise(result),
        }
        for result in results
    ]


def _matrices(results: list[MetricResult]) -> list[dict[str, Any]]:
    """Pair each two-dimensional result with the sentence that describes it.

    The matrix itself was assembled in the selector, totals and all. Nothing is
    computed here — a view that added up a column would be a second arithmetic
    for a number the selector already produced (brief 54).

    A result whose matrix is ``None`` is kept rather than filtered out. It
    declined, and the partial renders that refusal in words; dropping it would
    leave a section heading with nothing beneath it and no explanation of why.
    """
    return [
        {"result": result, "matrix": result.matrix, "summary": charts.describe_matrix(result)}
        for result in results
    ]


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------


@gate_required
def overview(request: HttpRequest) -> HttpResponse:
    """Ülevaade — counts and tables, and nothing that has to be interpreted.

    The v2 design took the charts off this page. What replaced them is the same
    catalogued metrics read as tables: `Uued teemad kuude lõikes`, `Teemad
    valdkonniti` and `Menetlusliigid` are three existing metric results whose
    segments each carry the list they counted, printed as rows instead of bars
    (02-EKRAANID §E).

    Beside them, two columns of plain counts and a foldout that says what the
    words mean. No chart, no target, no comparison with a colleague.
    """
    context = reporting_context.from_request(request)
    viewer = viewer_for(request)
    today = timezone.localdate()

    cards = compute_many_by_key(OVERVIEW_STRIP_METRICS, context)
    tables = [
        services.compute(metric_keys.NEW_NATIVE_FULL_MATTERS_BY_MONTH, context),
        services.compute(metric_keys.MATTERS_BY_POLICY_AREA, context),
        services.compute(metric_keys.MATTERS_BY_TRACK, context),
    ]
    period_label = "kõik aastad" if context.period.is_all else context.period.label

    return render(
        request,
        "reporting/overview.html",
        {
            **_shell(request, context, "ulevaade"),
            "seis": overview_strip.strip(cards, viewer, today, period_label),
            "tables": tables,
            "rail": overview_strip.rail(viewer, today, cards),
            "people": services.compute(metric_keys.MATTERS_BY_OWNER, context),
            "export_url": exports.export_url(context, "teemad"),
        },
    )


@gate_required
def matters(request: HttpRequest) -> HttpResponse:
    context = reporting_context.from_request(request)
    page = services.matters_page(context)

    return render(
        request,
        "reporting/matters.html",
        {
            **_shell(request, context, "teemad"),
            "cards": page.cards,
            "trends": _trends(page.trends),
            "matrices": _matrices(page.matrices),
            "compositions": _bars(page.charts),
            "organisation_tables": _bars(page.tables),
            "export_url": exports.export_url(context, "teemad"),
        },
    )


@gate_required
def activity(request: HttpRequest) -> HttpResponse:
    """Koja tegevus — canonical Submissions, then the archive, kept apart.

    The archive block has its own heading, its own date basis and its own
    wording. An archived letter is evidence Koda holds; a Submission is Koda
    asserting it sent something. Rendering them in one section would invite
    exactly the addition the data model refuses (brief 24, 39).
    """
    context = reporting_context.from_request(request)
    page = services.activity_page(context)

    return render(
        request,
        "reporting/activity.html",
        {
            **_shell(request, context, "tegevus"),
            "cards": page.cards,
            "trends": _trends(page.trends),
            "compositions": _bars(page.charts),
            "archive_trends": _trends(page.groups.get("archive_trends", [])),
            "archive_compositions": _bars(page.groups.get("archive_charts", [])),
            "archive_matrices": _matrices(page.groups.get("archive_matrices", [])),
            "recipient_tables": _bars(page.tables),
            "export_url": exports.export_url(context, "arvamused"),
        },
    )


@gate_required
def historical_materials(request: HttpRequest) -> HttpResponse:
    context = reporting_context.from_request(request)
    page = services.historical_page(context)
    trend_keys = {metric_keys.LEGACY_SOURCE_PAGES_BY_YEAR}

    return render(
        request,
        "reporting/historical.html",
        {
            **_shell(request, context, "ajalooline"),
            "cards": page.cards,
            "trends": _trends([r for r in page.charts if r.key in trend_keys]),
            "compositions": _bars([r for r in page.charts if r.key not in trend_keys]),
            "distributions": page.tables,
            "export_url": exports.export_url(context, "materjalid"),
        },
    )


@gate_required
def data_quality(request: HttpRequest) -> HttpResponse:
    context = reporting_context.from_request(request)
    page = services.quality_page(context)

    return render(
        request,
        "reporting/quality.html",
        {
            **_shell(request, context, "andmekvaliteet"),
            "cards": page.cards,
            "compositions": _bars(page.charts),
            "extraction": page.tables,
            "extraction_states": charts.bars(
                _extraction_chart(context),
            ),
            "queues": context.shared("quality.queues", lambda: quality.queues(context)),
            "deferred": sorted(DEFERRED_METRICS.items()),
            "export_url": exports.export_url(context, "andmekvaliteet"),
        },
    )


def _extraction_chart(context: ReportingContext) -> MetricResult:
    """The five extraction states as one composition, adding up to the total.

    Assembled here rather than as a catalogue metric because it is a *view* of
    five metrics that each have their own definition, not a sixth measurement.
    Giving it a definition of its own would put a number in the catalogue whose
    meaning is "the other five, side by side".
    """
    total = context.shared(
        "documents.visible_versions", lambda: documents.visible_versions(context).count()
    )
    return MetricResult(
        definition=definition(metric_keys.EXTRACTION_ELIGIBLE),
        value=total,
        population_count=total,
        segments=documents.extraction_states(context),
        as_of=context.now,
    )


# ---------------------------------------------------------------------------
# Drill-through lists
# ---------------------------------------------------------------------------


def _uuid_param(request: HttpRequest, name: str) -> uuid.UUID | None:
    raw = request.GET.get(name, "").strip()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise Http404("Tundmatu viide.") from None


def _year_param(request: HttpRequest) -> int | None:
    """``?aasta=`` as a supported year, or nothing.

    This surface refuses rather than dropping the filter, because it is a
    drill-through: arriving here from a chart with a year that cannot be read
    means the link was edited, and showing every year instead would answer a
    question nobody asked. A year outside the supported range is the same kind
    of unreadable as a word — it used to pass through and raise inside the ORM
    instead (CORR-02).
    """
    raw = request.GET.get("aasta", "").strip()
    if not raw:
        return None
    year = year_from(raw)
    if year is None:
        raise Http404("Tundmatu aasta.")
    return year


@gate_required
def submissions_list(request: HttpRequest) -> HttpResponse:
    """Sent Submissions, as this statistic counted them.

    This was once the product's only list of them, and is no longer: Arvamused
    is a workspace of its own (app/submissions/workspace_views.py). It stays,
    because a drill-through has to be *the statistic's own* population — same
    reporting context, same filters, same period — and the workspace answers a
    different question with a different default. Sending a reader from a chart
    to a list built by other rules is how a number and the rows behind it start
    disagreeing (brief 39).
    """
    context = reporting_context.from_request(request)
    selection = {
        "year": _year_param(request),
        "recipient_id": _uuid_param(request, "saaja"),
        "kind": request.GET.get("arvamus", "").strip(),
    }
    rows = submission_selectors.list_rows(context, **selection)
    paginator = Paginator(rows, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("leht"))

    query = request.GET.copy()
    query.pop("leht", None)

    return render(
        request,
        "reporting/submission_list.html",
        {
            **_shell(request, context, "tegevus"),
            "page": page,
            "paginator": paginator,
            "total": paginator.count,
            "query_string": query.urlencode(),
            "export_url": exports.export_url(
                context,
                "arvamused",
                aasta=request.GET.get("aasta", ""),
                saaja=request.GET.get("saaja", ""),
                arvamus=request.GET.get("arvamus", ""),
            ),
        },
    )


@gate_required
def materials_list(request: HttpRequest) -> HttpResponse:
    """Historical resource occurrences, filtered exactly as the chart was."""
    context = reporting_context.from_request(request)
    # `sektsioon` is deliberately absent: it is a context dimension and reaches
    # the population through `visible_pages`, so the list and the tab's numbers
    # cannot be narrowed differently.
    selection = {
        "file_type": request.GET.get("failityyp", "").strip().upper(),
        "state": request.GET.get("seisund", "").strip(),
    }
    if selection["state"] and selection["state"] not in historical.MATERIALISATION_LABELS:
        raise Http404("Tundmatu seisund.")

    rows = historical.list_rows(context, **selection)
    paginator = Paginator(rows, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("leht"))

    query = request.GET.copy()
    query.pop("leht", None)

    return render(
        request,
        "reporting/material_list.html",
        {
            **_shell(request, context, "ajalooline"),
            "page": page,
            "paginator": paginator,
            "total": paginator.count,
            "selection": selection,
            "state_label": historical.MATERIALISATION_LABELS.get(selection["state"], ""),
            "query_string": query.urlencode(),
            "export_url": exports.export_url(
                context,
                "materjalid",
                failityyp=selection["file_type"],
                seisund=selection["state"],
            ),
        },
    )


# ---------------------------------------------------------------------------
# Definitions and exports
# ---------------------------------------------------------------------------


@gate_required
def definitions(request: HttpRequest) -> HttpResponse:
    """The whole catalogue, as the product's own reference page.

    "Kuidas arvutatakse?" on a card opens the entry inline; this is the same
    content as a page, so a definition can be linked to in a message without
    asking somebody to hunt for the card it belongs to.
    """
    context = reporting_context.from_request(request)
    return render(
        request,
        "reporting/definitions.html",
        {
            **_shell(request, context, "definitsioonid"),
            "definitions": sorted(CATALOGUE.values(), key=lambda item: item.label_et),
            "deferred": sorted(DEFERRED_METRICS.items()),
        },
    )


@gate_required
def export(request: HttpRequest, slug: str) -> StreamingHttpResponse:
    """One CSV, from the same selectors and the same filters as the page."""
    context = reporting_context.from_request(request)

    if slug == "teemad":
        return exports.matters_csv(context)
    if slug == "arvamused":
        return exports.submissions_csv(
            context,
            year=_year_param(request),
            recipient_id=_uuid_param(request, "saaja"),
            kind=request.GET.get("arvamus", "").strip(),
        )
    if slug == "materjalid":
        return exports.materials_csv(
            context,
            file_type=request.GET.get("failityyp", "").strip().upper(),
            state=request.GET.get("seisund", "").strip(),
        )
    if slug == "andmekvaliteet":
        return exports.quality_csv(context)
    raise Http404("Tundmatu eksport.")
