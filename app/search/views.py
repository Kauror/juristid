"""Global search.

The view stays thin: it asks ``app.search.services`` and renders what comes
back. The count is taken from the database rather than from the length of the
rendered page, because those are different numbers as soon as the result set is
longer than one screen — and the count is the thing a lawyer uses to decide
whether their search was any good.

Stage 2B widens what can come back: a result may now be a Matter, an entry, a
sent opinion, or page 14 of an annex. Each row carries where it came from and
links as close to that as a server-rendered page can get.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.db.models import prefetch_related_objects
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from app.matters.models import Matter
from app.matters.selectors import current_action_of, open_action_prefetch
from app.search.models import SearchSourceKind
from app.search.services import (
    MATCH_REFERENCE,
    MAX_QUERY_CHARACTERS,
    MAX_RESULTS,
    clean_query,
    result_count,
    search,
    search_documents,
    search_matters,
)


@login_required
def search_view(request: HttpRequest) -> HttpResponse:
    raw = request.GET.get("q") or ""
    # The service decides what it is willing to act on, and for a hand-built URL
    # — control characters, or a query longer than it accepts — that is nothing.
    # The page has to be able to say *that* happened, because "vasteid ei
    # leitud" for a query which was never run is the exact false negative this
    # search exists to prevent.
    query = clean_query(raw)
    refused = bool(raw.strip()) and not query

    results = search(query=query, user=request.user) if query else []
    # Counted with the same authorized queryset that produced the rows, so the
    # number can never describe a result set the user is not allowed to have.
    total = result_count(query=query, user=request.user) if query else 0

    # A reference that resolves to exactly one Matter is a navigation, not a
    # search: typing 2026_184 means "open that file".
    if total == 1 and len(results) == 1 and results[0].match_kind == MATCH_REFERENCE:
        return redirect("matters:matter_detail", pk=results[0].matter.pk)

    # One query for the whole page instead of one per Matter row. `search`
    # returns detached objects, so the prefetch is attached to them here rather
    # than declared on the queryset — `current_action_of` reads exactly this
    # attribute and falls back to a query per Matter without it.
    matters = [result.matter for result in results if result.is_matter]
    if matters:
        prefetch_related_objects(matters, open_action_prefetch(request.user))

    rows = [
        {
            "result": result,
            "action": current_action_of(result.matter, request.user) if result.is_matter else None,
            "target": _target_url(result),
        }
        for result in results
    ]

    return render(
        request,
        "search/results.html",
        {
            # The cleaned term, not the raw one: a refused query echoed back
            # into the search box would sit there looking like something that
            # was searched for, and a 100,000-character one would be rendered
            # into the page in full.
            "query": query,
            "rows": rows,
            "result_count": total,
            "shown_count": len(rows),
            "is_truncated": total > len(rows),
            "result_limit": MAX_RESULTS,
            "query_was_refused": refused,
            "max_query_characters": MAX_QUERY_CHARACTERS,
            "nav_active": "otsing",
        },
    )


def _target_url(result: object) -> str:
    """Where clicking this result goes — as close to the source as practical.

    Server-rendered URLs and anchors, not a client-side router: the timeline is
    already one page with one anchor per entry, and a document already has a
    detail page. Building a navigation layer to get a few hundred pixels closer
    would be a large amount of machinery for a scroll position
    (Stage-2B brief 77).
    """
    kind = result.source_kind  # type: ignore[attr-defined]
    matter_url = reverse("matters:matter_detail", kwargs={"pk": result.matter.pk})  # type: ignore[attr-defined]

    if kind == SearchSourceKind.DOCUMENT_FRAGMENT and result.document_id:  # type: ignore[attr-defined]
        return reverse("documents:document_detail", kwargs={"pk": result.document_id})  # type: ignore[attr-defined]
    if kind == SearchSourceKind.ENTRY and result.entry_id:  # type: ignore[attr-defined]
        return f"{matter_url}#sissekanne-{result.entry_id}"  # type: ignore[attr-defined]
    if kind == SearchSourceKind.LEGACY_SOURCE_PAGE and result.source_page_id:  # type: ignore[attr-defined]
        return reverse(
            "legacy_import:source_page",
            kwargs={"pk": result.source_page_id},  # type: ignore[attr-defined]
        )
    if kind == SearchSourceKind.SUBMISSION and result.submission_id:  # type: ignore[attr-defined]
        position = reverse("matters:matter_position", kwargs={"pk": result.matter.pk})  # type: ignore[attr-defined]
        return f"{position}#arvamus-{result.submission_id}"  # type: ignore[attr-defined]
    return matter_url


# -- live suggestions in the header -------------------------------------------

#: How many suggestions the header dropdown offers.
#:
#: Five, and enforced in SQL rather than by slicing a longer list: this endpoint
#: answers a keystroke, and the difference between `LIMIT 6` and fetching fifty
#: rows to throw forty-five away is the difference between a dropdown and a
#: reason not to have one.
SUGGESTION_LIMIT = 5

#: The shortest query this endpoint will run.
#:
#: One character matches most of the corpus and ranks it by nothing anybody
#: typed, so the dropdown would open full of noise on the first keypress. The
#: browser does not ask below this either; this is the boundary, not a hint.
MIN_SUGGESTION_CHARACTERS = 2


@login_required
def suggestions(request: HttpRequest) -> JsonResponse:
    """The header search's live results — the same search, five rows of it.

    Deliberately thin. Every decision about *what* may come back is already
    made in ``app.search.services``: ``search_matters`` is the picker-shaped
    entry point that module has always offered, it goes through
    ``visible_documents`` like everything else, and the tiers it orders by are
    the tiers the full results page shows. There is no query here, no
    authorization predicate here, and nothing in the browser is trusted to
    filter anything — a second implementation of any of those would be a second
    opinion about who may see what, and the two would drift
    (docs/adr/0005, 0038, Stage-2B brief 43–44).

    Matter rows only. The full page answers with entries, sent opinions and page
    14 of an annex as well; a dropdown under a 320px field is a way to *open a
    file*, and mixing five kinds of target into it makes the one thing it is for
    harder. What the corpus holds beyond these five is one row away —
    «Vaata kõiki tulemusi» — rather than absent.

    ``login_required``, exactly like the page the form posts to. In shared-gate
    mode a session that has not chosen a persona is redirected there as well, so
    the dropdown can never answer a question the full search would refuse.
    """
    query = clean_query(request.GET.get("q") or "")
    full_url = reverse("search:search")

    if len(query) < MIN_SUGGESTION_CHARACTERS:
        # Not "no results" — nothing was asked. The two are different and the
        # browser renders them differently: this closes the dropdown, an empty
        # result set says so.
        return JsonResponse({"query": query, "results": [], "has_more": False, "all_url": ""})

    # One row more than is shown, so "is there more behind this" is answered by
    # the same LIMIT instead of a second COUNT over the whole match set.
    found = search_matters(query=query, user=request.user, limit=SUGGESTION_LIMIT + 1)
    shown = found[:SUGGESTION_LIMIT]

    return JsonResponse(
        {
            "query": query,
            "results": [
                {
                    "title": result.matter.title,
                    "url": reverse("matters:matter_detail", kwargs={"pk": result.matter.pk}),
                    "context": _suggestion_context(result.matter),
                }
                for result in shown
            ],
            "has_more": len(found) > SUGGESTION_LIMIT or _other_kinds_match(query, request.user),
            "all_url": f"{full_url}?{urlencode({'q': query})}",
        }
    )


def _suggestion_context(matter: Matter) -> str:
    """The secondary line: facts this Matter already carries, or nothing.

    Owner, addressee and stage, in that order, and only the ones that are
    actually set — an archive record frequently has none of them, and a row
    reading "· ·" would be the dropdown inventing punctuation to look complete.

    Every field is already joined by ``visible_documents``'s ``select_related``,
    so this costs no query per row however many rows there are.

    No reference. A Matter is named by its title everywhere in this product;
    printing `2026_184` beside it in one dropdown would reintroduce the
    identifier every ordinary reading surface deliberately stopped showing
    (`app/matters/views.py`, `_immutable_facts`; review of PR #72 §2).
    """
    owner = matter.owner
    addressee = matter.addressee_organisation
    stage = matter.stage
    parts = [
        owner.display_name if owner else "",
        addressee.name if addressee else "",
        stage.label_et if stage else "",
    ]
    return " · ".join(part for part in parts if part)


def _other_kinds_match(query: str, user: Any) -> bool:
    """Whether the full page would show something this dropdown does not.

    An entry, a sent opinion or a page of an annex can match a query that
    reaches five matters or fewer, and «Vaata kõiki tulemusi» claiming there is
    more when there is not — or hiding that there is — are both the endpoint
    saying something untrue about the corpus.

    ``exists()`` rather than a count: PostgreSQL stops at the first row, and
    Django drops the ordering for the subquery, so this is a bounded probe and
    not a second search.
    """
    return (
        search_documents(query=query, user=user)
        .exclude(source_kind=SearchSourceKind.MATTER)
        .exists()
    )
