"""«Lisa teema»: the topics a reader may add to a report by hand.

The header search's own ranking and boundary first (`search_matters`, which
resolves the reader's scope before it filters), then a plain title match over
the same readable, real Matters (`sources.readable_matters`) — so a topic the
projection has not indexed yet is still found by its name. Read only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.matters.models import Matter
from app.reporting.report_builder.draft import MIN_SEARCH_CHARACTERS, SEARCH_LIMIT
from app.reporting.report_builder.sources import readable_matters
from app.search.services import clean_query, search_matters


@dataclass(frozen=True)
class Found:
    matter_id: uuid.UUID
    title: str
    reference: str


def _reference(matter: Matter) -> str:
    if matter.reference_year and matter.reference_number:
        return f"{matter.reference_year}/{matter.reference_number}"
    return ""


def matters(user: object, raw_query: str, *, exclude: set[uuid.UUID]) -> list[Found]:
    """Up to `SEARCH_LIMIT` readable, real Matters matching ``raw_query``."""
    query = clean_query(raw_query or "")
    if len(query) < MIN_SEARCH_CHARACTERS:
        return []
    readable = readable_matters(user)
    ranked = [result.matter.pk for result in search_matters(query=query, user=user, limit=30)]
    by_title = list(
        readable.filter(title__icontains=query)
        .exclude(pk__in=ranked)
        .order_by("title", "pk")
        .values_list("pk", flat=True)[:30]
    )
    # A topic whose title holds the whole typed text comes first: a person who
    # typed a title must see that topic even when the search projection has not
    # indexed it yet and ten other ranked topics share its words.
    titled = set(readable.filter(title__icontains=query).values_list("pk", flat=True)[:30])
    first = [pk for pk in ranked if pk in titled] + by_title
    wanted = [pk for pk in first + ranked if pk not in exclude]
    wanted = list(dict.fromkeys(wanted))
    # `readable` again: the search projection is scoped, but data class is
    # this report's own rule, and the second read is what states it.
    found = {matter.pk: matter for matter in readable.filter(pk__in=wanted)}
    return [
        Found(matter_id=pk, title=found[pk].title, reference=_reference(found[pk]))
        for pk in wanted
        if pk in found
    ][:SEARCH_LIMIT]
