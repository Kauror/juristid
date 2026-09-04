"""The Arvamused workspace — two tabs over two genuinely different sources.

Kept in its own module rather than added to ``app/submissions/views.py``,
because that file is a set of write actions on one Matter's submission and this
is a reading destination for the whole department. Mixing them would put a
``@gate_required`` list beside a ``@login_required`` POST and invite the next
person to copy whichever decorator was nearest.

**Saadetud** is canonical: ``Submission`` rows, ``visible_to`` the reader,
defaulting to SENT. Production holds zero of them today, and the empty state
says why rather than looking like a failed query.

**Arhiivikirjad** is historical evidence: the 767 held letters, read through the same
``search_archive`` the administrative browse uses. It is offered only to readers
``may_read_archive`` admits, and the route refuses a crafted URL regardless.
Nothing here widens that boundary; ``docs/adr/0028`` decides it, and a workspace
wanting a fuller-looking page is not a reason to revisit an access decision.

The two tabs never merge. An archive letter is not a Submission, and the day P4
canonicalises the defensible ones it will create real Submission rows that
appear in Saadetud on their own — which is why this module has no code that
would then need removing.
"""

from __future__ import annotations

from typing import Any

from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from app.core.decorators import gate_required, viewer_for
from app.legacy_import.opinion_access import may_read_archive, require_archive_reader
from app.legacy_import.opinion_search import (
    ArchiveFilters,
    ArchiveQueryRefused,
    archive_counts,
    search_archive,
    visible_archive,
)
from app.submissions import workspace
from app.submissions.embedded import embedded_context, page_url
from app.submissions.enums import SubmissionKind, SubmissionStatus
from app.submissions.workspace import PAGE_SIZE, SentFilters, SubmissionQueryRefused

#: The workspace's tabs, in the order a reader should meet them: what this
#: system recorded, then what the department sent before it existed.
TABS: tuple[tuple[str, str, str], ...] = (
    ("saadetud", "Saadetud", "submissions:sent"),
    ("arhiiv", "Arhiivikirjad", "submissions:archive"),
)


def _shell(request: HttpRequest, viewer: Any, tab: str) -> dict[str, Any]:
    """Everything both tabs need around their own content.

    ``can_read_archive`` is computed once here and used both to render the tab
    strip and to caption it. A reader who may not open the archive is told that
    it exists and that it is administrative — silently dropping the tab would
    read as "there is no archive", which is a different and untrue statement.
    """
    can_read_archive = may_read_archive(viewer)
    return {
        "tabs": TABS,
        "active_tab": tab,
        "can_read_archive": can_read_archive,
        "archive_total": archive_counts(viewer).get("total", 0) if can_read_archive else 0,
        "sent_counts": workspace.sent_counts(viewer),
        # Teemad, not an `arvamused` of its own: the bar no longer carries an
        # Arvamused item, and a page that marked a destination nobody can see
        # would leave the bar with nothing current on it while the reader is
        # plainly somewhere. Arvamused is part of the Teemad area now, and the
        # bar says which area they are in — which is what `is-active` has always
        # meant here (docs/adr/0047).
        "nav_active": "teemad",
    }


@gate_required
def sent(request: HttpRequest) -> HttpResponse:
    """Saadetud — the canonical record of what Koda sent from this system."""
    viewer = viewer_for(request)
    filters = SentFilters(
        query=request.GET.get("q", "").strip(),
        year=request.GET.get("aasta", "").strip(),
        month=request.GET.get("kuu", "").strip(),
        status=request.GET.get("olek", SubmissionStatus.SENT).strip(),
        kind=request.GET.get("liik", "").strip(),
        recipient_id=request.GET.get("saaja", "").strip(),
        owner_id=request.GET.get("vastutaja", "").strip(),
    )

    refusal = ""
    rows: Any = []
    try:
        rows = workspace.sent_queryset(viewer, filters)
    except SubmissionQueryRefused as error:
        # Reported on the page rather than raised. A mistyped year is a typo,
        # and a 500 would lose the rest of the reader's filter state.
        refusal = str(error)
        rows = []

    paginator = Paginator(rows, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("leht"))

    query = request.GET.copy()
    query.pop("leht", None)

    return render(
        request,
        "submissions/sent.html",
        {
            **_shell(request, viewer, "saadetud"),
            "filters": filters,
            "refusal": refusal,
            "page": page,
            "total": paginator.count if not refusal else 0,
            "query_string": query.urlencode(),
            "years": workspace.sent_years(viewer),
            "recipients": workspace.recipient_options(viewer),
            "kinds": SubmissionKind.choices,
            "statuses": SubmissionStatus.choices,
        },
    )


@gate_required
def archive(request: HttpRequest) -> HttpResponse:
    """Arhiivikirjad — the held historical letters, read-only.

    Read-only on purpose, and narrower than the administrative browse it shares
    a search function with: no candidate state, no link form, no withdraw
    action. Filing a letter onto a Matter is a business judgement with its own
    reviewer set (``may_manage_archive_links``); discovering that a letter
    exists is not, and conflating the two here would put a write control in
    front of every reader who may look.
    """
    viewer = viewer_for(request)
    # Raise rather than render an empty list. An empty archive tab and a refused
    # one look identical to a reader, and only one of them is true.
    require_archive_reader(viewer)

    filters = ArchiveFilters(
        query=request.GET.get("q", "").strip(),
        year=request.GET.get("aasta", "").strip(),
        linked=request.GET.get("seotud", "").strip(),
    )

    refusal = ""
    rows: Any = []
    try:
        rows = search_archive(user=viewer, filters=filters)
    except ArchiveQueryRefused as error:
        refusal = str(error)
        rows = []

    paginator = Paginator(rows, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("leht"))

    query = request.GET.copy()
    query.pop("leht", None)

    counts = archive_counts(viewer)

    return render(
        request,
        "submissions/archive.html",
        {
            **_shell(request, viewer, "arhiiv"),
            "filters": filters,
            "refusal": refusal,
            "page": page,
            "total": paginator.count if not refusal else 0,
            "query_string": query.urlencode(),
            "counts": counts,
            "unlinked_count": counts["total"] - counts["linked"],
            "years": _archive_years(viewer),
            # Whether the corpus can be searched by what the letters *say*.
            # Every held text is BLOCKED in production by the Secure Pilot Gate,
            # so the page says so instead of offering a body search that would
            # return nothing and read as "Koda never wrote that".
            "body_search_available": counts["with_body"] > 0,
        },
    )


@gate_required
def embedded_block(request: HttpRequest) -> HttpResponse:
    """The Arvamused section's results, for the Teemad page's live search.

    `gate_required` and nothing else, matching the two tabs above: the block
    reads exactly what they read, through the same selectors, for the same
    viewer. It is deliberately *not* `require_archive_reader` — the section
    resolves an archive request it may not serve down to Saadetud rather than
    refusing, because this fragment answers a box on somebody else's page
    (``app/submissions/embedded.py``).

    ``HX-Push-Url`` carries the *Teemad* address the answer belongs to, never
    this route's own. The section is part of a page, and after a live search the
    browser must be holding a URL somebody can paste: what is on screen, what
    the address bar says and what a colleague receives are one thing, and
    without this header the third of those silently lags the first two.

    Pushed rather than replaced, matching the register's own live search, so
    Back steps through an opinion search exactly as it steps through a register
    one. htmx does the pushing and therefore keeps its own history snapshot in
    step; composing the address here rather than in the browser keeps one
    definition of what the page's state is (``embedded.page_url``).
    """
    response = render(
        request, "submissions/partials/embedded_results.html", embedded_context(request)
    )
    response["HX-Push-Url"] = page_url(request.GET)
    return response


def _archive_years(viewer: Any) -> list[int]:
    """Years the visible archive actually covers, newest first.

    Read through ``visible_archive`` rather than the model manager, so a reader
    who may not open the archive cannot learn its date range from a filter
    control.
    """
    values = (
        visible_archive(viewer)
        .exclude(source_year__isnull=True)
        # `.order_by()` is load-bearing, not tidying: Django adds a DISTINCT
        # query's ordering expressions to its SELECT, and the projection orders
        # by `document_date`, `created_at` and the primary key. Without it every
        # letter is its own row and one year is offered once per letter. The
        # output order is this function's own, below.
        .order_by()
        .values_list("source_year", flat=True)
        .distinct()
    )
    return sorted((year for year in values if year is not None), reverse=True)
