"""The Arvamused workspace as a bounded section under the Teemad register.

A Teema is the object the department works on; an arvamus is usually one of the
things that work finishes with. So ``Arvamused`` is no longer a destination of
its own on the bar — it is a second section on the Teemad page, underneath the
register (``docs/adr/0047``).

What this module is *not* is a second opinion workspace. Every population it
returns comes from the selectors the standalone workspace already uses —
``workspace.sent_queryset`` for the canonical record, ``search_archive`` for the
held historical letters — so there is one definition of "what may this reader
see" and one of "what does this search match". This module composes; it does not
decide.

Three properties it does own, and they are the ones a review should check:

**The two searches never touch.** The register reads ``?q=``; this reads
``?arvamus_q=``. Both live in the same address and neither is reachable by the
other's parameter name, so typing in one box cannot narrow the other list — and
a link somebody pastes carries both states exactly as they were on screen.

**The archive boundary is asked before anything is counted.**
``may_read_archive`` decides whether the Arhiiv tab exists *and* whether the
archive is queried at all. A reader who may not read it cannot reach archive
rows, an archive count, or the corpus's date range by hand-editing
``?arvamus_vaade=arhiiv`` — that value falls back to Saadetud in Python, before
a query is built. It does not raise: a crafted opinion parameter must not take
the register down with it.

**It is bounded, and says so.** :data:`EMBEDDED_ROWS` rows, no pager. The full
count is printed beside them and «Vaata kõiki arvamusi» opens the standalone
workspace carrying the same search — the section is a way in, not a replacement
for the destination it links to.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, QueryDict
from django.urls import reverse
from django.utils.http import urlencode

from app.core.decorators import viewer_for
from app.legacy_import.opinion_access import may_read_archive
from app.legacy_import.opinion_search import (
    ArchiveFilters,
    ArchiveQueryRefused,
    search_archive,
    visible_archive,
)
from app.submissions import workspace
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.submissions.workspace import SentFilters, SubmissionQueryRefused

#: How many opinion rows the embedded section shows before it hands over.
#:
#: Twelve rather than the workspace's fifty. This list sits *under* a register
#: page that is already fifty rows long, and a second fifty-row table would make
#: the page it is a section of unreadable — which is the one thing this
#: consolidation must not do. Twelve is enough to recognise recent work in and
#: to make a search feel like it answered, and short enough that the section
#: fits a screen.
EMBEDDED_ROWS = 12

#: Which source the section is showing. Named apart from a bare ``?vaade=``
#: because this value shares one address with the whole register, and
#: ``vaade`` is already Ülevaade's.
VIEW_PARAM = "arvamus_vaade"

#: The opinion search term. The register owns ``?q=``; this is deliberately not
#: it, and nothing in this module reads ``?q=``.
QUERY_PARAM = "arvamus_q"

SENT_VIEW = "saadetud"
ARCHIVE_VIEW = "arhiiv"

#: Parameters this section owns. Everything else in the address belongs to the
#: register and travels with the opinion form untouched, so searching opinions
#: does not silently drop the year and the owner somebody had already picked.
OWN_PARAMS = frozenset({VIEW_PARAM, QUERY_PARAM})


def requested_view(params: Any, *, can_read_archive: bool) -> str:
    """Which source to read, after the access question rather than before it.

    An unknown value, and ``arhiiv`` from a reader who may not read the archive,
    both resolve to Saadetud. Refusing with a 403 would be the standalone
    workspace's answer and it is the wrong one here: this section is a passenger
    on the register, and a mistyped opinion parameter must not take the whole
    Teemad page away from somebody who was looking at teemad.
    """
    requested = (params.get(VIEW_PARAM) or "").strip().lower()
    if requested == ARCHIVE_VIEW and can_read_archive:
        return ARCHIVE_VIEW
    return SENT_VIEW


def _sent_rows(viewer: Any, query: str) -> tuple[Any, int, str]:
    """Canonical submissions, bounded. Counted before slicing."""
    try:
        rows = workspace.sent_queryset(viewer, SentFilters(query=query))
    except SubmissionQueryRefused as error:
        # Reported inside the section rather than raised. The register above it
        # answered correctly and must keep its answer.
        return [], 0, str(error)
    return rows[:EMBEDDED_ROWS], rows.count(), ""


def _archive_rows(viewer: Any, query: str) -> tuple[Any, int, str]:
    """Held historical letters, bounded. ``search_archive`` asks the boundary."""
    try:
        rows = search_archive(user=viewer, filters=ArchiveFilters(query=query))
    except ArchiveQueryRefused as error:
        return [], 0, str(error)
    return rows[:EMBEDDED_ROWS], rows.count(), ""


def embedded_context(request: HttpRequest) -> dict[str, Any]:
    """Everything the Arvamused section needs, for the page and the fragment.

    One builder for both, so the block a keystroke swaps in cannot disagree with
    the block the page rendered — the same convention the register states for
    its own results fragment (``app/matters/views.py``).
    """
    viewer = viewer_for(request)
    params = request.GET

    can_read_archive = may_read_archive(viewer)
    view = requested_view(params, can_read_archive=can_read_archive)
    query = (params.get(QUERY_PARAM) or "").strip()

    if view == ARCHIVE_VIEW:
        rows, total, refusal = _archive_rows(viewer, query)
    else:
        rows, total, refusal = _sent_rows(viewer, query)

    # One count each, not the workspace's full headline sets.
    #
    # The section is a passenger on a page that already does plenty, so it asks
    # for exactly what it prints: the Saadetud tab's figure, and the Arhiiv
    # tab's. ``workspace.sent_counts`` answers three questions and
    # ``archive_counts`` four; six of those seven would be computed for a strip
    # with two numbers on it. The standalone workspace still uses both, where
    # the rest of the figures are actually shown.
    sent_count = Submission.objects.visible_to(viewer).filter(status=SubmissionStatus.SENT).count()
    # Asked only of a reader who may read it, and through ``visible_archive``
    # rather than the manager: a refused reader must not learn the size of the
    # corpus from a tab they are not offered.
    archive_total = visible_archive(viewer).count() if can_read_archive else 0

    # Where «Vaata kõiki arvamusi» goes, carrying the same search. The full
    # workspace reads `?q=` — its own parameter, on its own page, with nothing
    # to do with the register's.
    full_url = reverse("submissions:archive" if view == ARCHIVE_VIEW else "submissions:sent")
    if query:
        full_url = f"{full_url}?{urlencode({'q': query})}"

    return {
        "opinion_view": view,
        "opinion_query": query,
        "opinion_rows": rows,
        "opinion_total": total,
        "opinion_refusal": refusal,
        "opinion_bound": EMBEDDED_ROWS,
        "opinion_sent_count": sent_count,
        "opinion_archive_total": archive_total,
        "opinion_can_read_archive": can_read_archive,
        "opinion_full_url": full_url,
        "opinion_view_param": VIEW_PARAM,
        "opinion_query_param": QUERY_PARAM,
        "opinion_archive_view": ARCHIVE_VIEW,
        "opinion_sent_view": SENT_VIEW,
        "opinion_fragment_url": reverse("submissions:embedded_block"),
        # The tab strip and the form's hidden inputs. Both are built from the
        # address the page was asked for, so switching source or searching
        # opinions carries the register's state through untouched.
        "opinion_sent_link": view_link(params, view=SENT_VIEW),
        "opinion_archive_link": view_link(params, view=ARCHIVE_VIEW),
        "opinion_register_state": register_state(params),
    }


def register_state(params: Any) -> list[tuple[str, str]]:
    """The register's own parameters, for the opinion form to carry unchanged.

    Without these, submitting the opinion search with JavaScript off would drop
    every filter the reader had applied to the register above it and the list
    would silently widen — the same failure the register's own search box
    carries hidden inputs to avoid.

    ``leht`` travels too, and deliberately: a page number is register state like
    any other here. Searching opinions is not a reason to send somebody back to
    the first page of teemad.
    """
    return [(name, value) for name, value in params.items() if name not in OWN_PARAMS and value]


def view_link(params: Any, *, view: str) -> str:
    """This page's address with the opinion section switched to ``view``.

    Built from the whole current query string, so a tab is a plain link that
    keeps every register filter, the register's page number and the opinion
    search exactly as they are — and lands on the section rather than the top of
    a page the reader has already scrolled past.
    """
    query = params.copy()
    query[VIEW_PARAM] = view
    return f"?{query.urlencode()}#arvamused"


def page_url(params: Any) -> str:
    """The Teemad address that describes what the section is currently showing.

    Returned as ``HX-Push-Url`` from the fragment route, so a live opinion
    search leaves the browser holding a URL a colleague can be sent. The
    invariant this exists for: what is on screen, what the address bar says, and
    what a pasted link reproduces are one thing.

    The path is built here, never taken from the request. The fragment answers
    at ``/arvamused/plokk/`` and that address must never reach the bar — it is
    a piece of a page, and a reader who pasted it would send somebody a table
    with no page around it (the trap ``matters.views._wants_fragment``
    documents for the register's own fragment).

    Empty values are dropped rather than written as ``arvamus_q=``: clearing the
    opinion box must take the parameter out of the address, not leave an empty
    one behind that reads as a filter which is still applied. ``arvamus_vaade``
    is written only when it is not the default, for the same reason — a bare
    address already means Saadetud, and a redundant parameter on every link is
    noise a reader has to learn to ignore.

    Everything else in ``params`` is the register's and is copied untouched, in
    the order it arrived: ``q``, the filters, and ``leht``. The register's live
    search pushes its own state into the address, and `static/js/app.js` keeps
    the opinion form's hidden inputs in step with it — without that, this would
    compose a *stale* register state and push it over the correct one, which is
    worse than not pushing at all.
    """
    query = QueryDict(mutable=True)
    for name, value in params.items():
        if name not in OWN_PARAMS and value:
            query[name] = value

    opinion_query = (params.get(QUERY_PARAM) or "").strip()
    if opinion_query:
        query[QUERY_PARAM] = opinion_query

    view = (params.get(VIEW_PARAM) or "").strip().lower()
    if view and view != SENT_VIEW:
        query[VIEW_PARAM] = view

    address = reverse("matters:matter_list")
    encoded = query.urlencode()
    return f"{address}?{encoded}" if encoded else address
