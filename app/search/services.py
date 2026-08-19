"""Stage-1 search.

A narrow, deliberately small boundary: callers ask :func:`search_matters` for
results and get back rows plus the reason each one matched. Stage 2 replaces the
implementation with a rebuildable ``SearchDocument`` projection over Estonian
full-text search and document text — and should be able to do that without the
navigation, the view or the template changing (docs/adr/0006).

What this does *not* do is as important: no document contents, no OCR, no
semantic similarity, no external engine. Those are Stage-2 decisions with their
own evidence requirements.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db.models import Func, Q, QuerySet, TextField, Value

from app.matters.models import Matter


class ArrayToString(Func):
    """PostgreSQL ``array_to_string``.

    ArrayField has no substring lookup, so alternate titles are flattened to
    text for the comparison.
    """

    function = "array_to_string"
    output_field = TextField()


#: Why a row came back. Shown in the results so a lawyer can see whether the
#: system understood the query the way they meant it.
MATCH_REFERENCE = "reference"
MATCH_TITLE = "title"
MATCH_ALTERNATE_TITLE = "alternate_title"
MATCH_ORGANISATION = "organisation"
MATCH_TAXONOMY = "taxonomy"

MATCH_LABELS: dict[str, str] = {
    MATCH_REFERENCE: "Viide",
    MATCH_TITLE: "Pealkiri",
    MATCH_ALTERNATE_TITLE: "Muu pealkiri",
    MATCH_ORGANISATION: "Asutus",
    MATCH_TAXONOMY: "Valdkond või silt",
}

MAX_RESULTS = 50


@dataclass(frozen=True)
class SearchResult:
    matter: Matter
    match_kind: str

    @property
    def match_label(self) -> str:
        return MATCH_LABELS.get(self.match_kind, "")


def _base_queryset(user: Any) -> QuerySet[Matter]:
    """Authorization first, always.

    Scoping happens before filtering, ordering, counting and slicing, so a
    restricted Matter cannot influence a result set, a count or a page boundary
    (master specification 5.2).
    """
    return (
        Matter.objects.visible_to(user)
        .select_related("owner", "stage", "source_organisation", "addressee_organisation")
        .prefetch_related("next_actions")
    )


def search_matters(*, query: str, user: Any, limit: int = MAX_RESULTS) -> list[SearchResult]:
    """Find Matters by reference, title, alternate title, organisation or tag."""
    term = (query or "").strip()
    if not term:
        return []

    visible = _base_queryset(user)
    results: list[SearchResult] = []
    seen: set[Any] = set()

    def collect(rows: QuerySet[Matter], kind: str) -> None:
        for matter in rows:
            if matter.pk in seen:
                continue
            seen.add(matter.pk)
            results.append(SearchResult(matter=matter, match_kind=kind))

    # 1. An exact human reference is unambiguous, so it ranks first and alone.
    parsed = Matter.parse_reference(term)
    if parsed is not None:
        year, number = parsed
        collect(
            visible.filter(reference_year=year, reference_number=number),
            MATCH_REFERENCE,
        )
        if results:
            return results[:limit]

    remaining = limit - len(results)
    if remaining > 0:
        collect(visible.filter(title__icontains=term)[:remaining], MATCH_TITLE)

    remaining = limit - len(results)
    if remaining > 0:
        # Alternate titles are how a renamed draft stays findable under the name
        # people still use for it.
        collect(
            visible.annotate(
                alternate_title_text=ArrayToString("alternate_titles", Value(" "))
            ).filter(alternate_title_text__icontains=term)[:remaining],
            MATCH_ALTERNATE_TITLE,
        )

    remaining = limit - len(results)
    if remaining > 0:
        collect(
            visible.filter(
                Q(source_organisation__name__icontains=term)
                | Q(addressee_organisation__name__icontains=term)
                | Q(source_organisation__aliases__alias__icontains=term)
                | Q(addressee_organisation__aliases__alias__icontains=term)
            ).distinct()[:remaining],
            MATCH_ORGANISATION,
        )

    remaining = limit - len(results)
    if remaining > 0:
        collect(
            visible.filter(
                Q(tags__name_et__icontains=term)
                | Q(tags__aliases__alias__icontains=term)
                | Q(policy_areas__name_et__icontains=term)
            ).distinct()[:remaining],
            MATCH_TAXONOMY,
        )

    return results[:limit]
