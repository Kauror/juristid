"""Which Teemad, in which order — the one pipeline the register table and its exports share.

Contract A of the 2026-11-02 package. The register used to decide its rows inside
the view (`matters.views.matter_list`): free text, then the status default, then
every structured filter, then the sort. A table export written beside it would
have been a second copy of that sequence, and two copies drift — the export
would one day hold rows the table does not, or miss a search word the table
applied. So the sequence lives here, once, and both read it:

* :func:`selection_from` reads the address into a :class:`RegisterSelection` —
  the requesting reader, the cleaned free text, the status with its default,
  the parameters (every value of every repeated key kept), and the sort.
* :meth:`RegisterSelection.searched` is the authorized population after free
  text, before the status — what an empty page asks «are the matches simply in
  another status» of.
* :meth:`RegisterSelection.filtered` adds every structured filter.
* :meth:`RegisterSelection.ordered` adds the deterministic order and the
  de-duplication a plural join needs. It is **unpaginated**: the table pages it,
  «Laadi Excel tabelis kuvatud infoga» writes all of it.

What the pipeline never does: apply text search in the browser, accept an ORM
path or ordering expression from the address (sorts are the keys of
:data:`SORT_FIELDS` and the four derived ones, anything else is the default),
or widen the result after an unreadable value — every filter empties the list
instead (`register_filters`).

The ``base`` is the caller's: the table passes
`selectors.matter_list_queryset` (with the row annotations it renders), an
export passes the same with its columns' prefetches. Both start from
``Matter.objects.visible_to(user)``, so authorization is decided before any
parameter is read.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db.models import F, QuerySet

from app.core.authorization import distinct_if_fans_out
from app.intelligence import commencements
from app.matters import activity, register_dates, register_filters, selectors
from app.matters.models import Matter
from app.search import services as search_services

#: The sorts that order on a stored column, as the column names to order by.
#:
#: Every one of these four is an address somebody may already hold. `reference`
#: is what a bare `/teemad/` means and what the Järjestus control posts;
#: `deadline` orders on `Matter.response_deadline` and is **not** the Kuupäev
#: column — the column shows the open step's own date whenever the visible step
#: has one, and the two coincide on only some of the register. Neither value's
#: meaning changes here: the interactive headings add values beside these rather
#: than redefining them, because a bookmark that silently starts answering a
#: different question is worse than one that stops working (brief 18).
SORT_FIELDS = {
    "reference": ("-reference_year", "-reference_number"),
    "title": ("title",),
    "updated": ("-updated_at",),
    "deadline": ("response_deadline",),
}

#: The register's ordering when nobody has chosen one. Newest reference first —
#: the department's own filing order, and the list a lawyer expects to open on.
#: A clickable heading does not change it: the two new columns are sortable, not
#: the new default (brief 19).
DEFAULT_SORT = "reference"

#: What the **Kuupäev** heading sorts by, in the order its activations cycle
#: through. The key is the effective date the row displays, never a hidden
#: column beside it (app/matters/register_dates.py).
DATE_SORT_ASC = "kuupaev_asc"
DATE_SORT_DESC = "kuupaev_desc"

#: What the **Viimane tegevus** heading sorts by. Named for the column rather
#: than for `?tegevus=`, which is a different dimension one column to the left:
#: `?tegevus=` narrows by the *next* step and this orders by the *last* thing
#: that happened, and one vocabulary answering both would be two questions
#: wearing one word (app/matters/views.py `FILTER_LABELS`).
ACTIVITY_SORT_NEWEST = "viimane_uusim"
ACTIVITY_SORT_OLDEST = "viimane_vanim"

#: Every sort the register accepts. Anything else in `?jarjestus=` is the default.
KNOWN_SORTS = frozenset(
    {*SORT_FIELDS, DATE_SORT_ASC, DATE_SORT_DESC, ACTIVITY_SORT_NEWEST, ACTIVITY_SORT_OLDEST}
)


def ordered(queryset: Any, sort: str, user: Any) -> Any:
    """The register in the chosen order, with the derived keys it needs.

    Two rules the headings rest on.

    **A missing date is last in both directions.** PostgreSQL's own default puts
    NULLs last ascending and *first* descending, so "latest first" would have
    opened on every row that has no date at all — a page of em dashes, above the
    rows somebody clicked the heading to see. ``nulls_last=True`` on both sides
    says what the column means instead: rows with a known date, in order, then
    the rows without one.

    **The derived keys are annotated only when they are ordered on.** Each costs
    a correlated subquery (`register_display_date`) or re-reads seven of them
    (`last_activity_on`), and the register's ordinary page reads neither — the
    row renders both facts from what
    ``selectors.matter_list_queryset`` has already loaded. A key a filter has
    already annotated (`?viimane_alates=`) is reused, not annotated twice.

    ``-created_at`` and then the primary key are the final tie-breaks for every
    sort, so two rows sharing a date — or a creation instant — have one stable
    order rather than whatever the planner returns (brief 22: a page boundary
    that moves between requests loses rows, and an export would disagree with
    the table it was taken from).
    """
    chosen: tuple[Any, ...]
    annotations = getattr(getattr(queryset, "query", None), "annotations", {})
    if sort in (DATE_SORT_ASC, DATE_SORT_DESC):
        if register_dates.DISPLAY_DATE not in annotations:
            queryset = register_dates.annotate_display_date(queryset, user)
        key = F(register_dates.DISPLAY_DATE)
        chosen = (key.asc(nulls_last=True) if sort == DATE_SORT_ASC else key.desc(nulls_last=True),)
    elif sort in (ACTIVITY_SORT_NEWEST, ACTIVITY_SORT_OLDEST):
        if activity.ACTIVITY_DATE not in annotations:
            queryset = activity.annotate_activity_date(queryset)
        key = F(activity.ACTIVITY_DATE)
        chosen = (
            key.desc(nulls_last=True) if sort == ACTIVITY_SORT_NEWEST else key.asc(nulls_last=True),
        )
    else:
        chosen = SORT_FIELDS.get(sort, SORT_FIELDS[DEFAULT_SORT])
    return queryset.order_by(*chosen, "-created_at", "-pk")


@dataclass(frozen=True)
class RegisterSelection:
    """One reader's register address, read once.

    ``params`` is what the reader actually has; ``status_params`` is the same
    with the status default written in — read wherever the *population* is
    decided — so every link built from ``params`` keeps «not chosen» as not
    chosen (docs/adr/0158 §2).
    """

    user: Any
    params: Any
    query: str
    query_was_refused: bool
    status: str
    status_defaulted: bool
    status_params: Any
    sort: str

    def searched(self, base: QuerySet[Matter]) -> QuerySet[Matter]:
        """``base`` narrowed by the free text — before the status and the filters.

        Through the search module's own gate (E-07): a query it refuses —
        longer than `MAX_QUERY_CHARACTERS` — is never run, and the caller says so.

        **Searched once per request** (QRY-06). Composed as a subquery, the
        search ran inside every statement that read the register — the rows, the
        count beside the box, and on an empty page `_matches_elsewhere` again —
        and each run is a scan of the whole projection. The Matters it reaches
        are bounded by the corpus, so they are read once here and the register
        narrows by the ids. Same population, same authorization: the search is
        scoped to this reader, and so is the queryset it narrows.
        """
        if self.query_was_refused:
            return base.none()
        if not self.query:
            return base
        matched = {
            matter_id
            for matter_id in search_services.matching_matter_ids(
                query=self.query, user=self.user
            ).values_list("matter_id", flat=True)
            if matter_id is not None
        }
        return base.filter(pk__in=matched)

    def filtered(
        self, searched: QuerySet[Matter], **options: Any
    ) -> tuple[QuerySet[Matter], dict[str, str]]:
        """``searched`` narrowed by every structured filter, and the date echo."""
        return register_filters.apply_register_filters(
            searched, self.user, self.status_params, **options
        )

    def ordered(self, filtered: QuerySet[Matter]) -> QuerySet[Matter]:
        """``filtered`` de-duplicated and in the chosen order — every row, no page."""
        return distinct_if_fans_out(ordered(filtered, self.sort, self.user))

    def rows(
        self, base: QuerySet[Matter], *, prepare: Callable[[Any], Any] | None = None
    ) -> QuerySet[Matter]:
        """The whole pipeline from an authorized ``base``: what the table pages through."""
        population, _echo = self.filtered(self.searched(base))
        if prepare is not None:
            population = prepare(population)
        return self.ordered(population)


def selection_from(user: Any, params: Any) -> RegisterSelection:
    """Read the register address. Nothing here touches the database."""
    raw_query = params.get("q") or ""
    query = search_services.clean_query(raw_query)
    status, status_defaulted = commencements.status_scope(params)
    status_params: Any = params
    if status_defaulted and status != "avatud":
        status_params = params.copy()
        status_params["olek"] = status
    sort = params.get("jarjestus", DEFAULT_SORT)
    return RegisterSelection(
        user=user,
        params=params,
        query=query,
        query_was_refused=bool(raw_query.strip()) and not query,
        status=status,
        status_defaulted=status_defaulted,
        status_params=status_params,
        sort=sort if sort in KNOWN_SORTS else DEFAULT_SORT,
    )


def register_rows(user: Any, params: Any) -> QuerySet[Matter]:
    """Every row ``matters:matter_list?<params>`` would page through, in its order.

    The table's own base (`selectors.matter_list_queryset`), so an export reads
    the same annotated population the rows were rendered from.
    """
    return selection_from(user, params).rows(selectors.matter_list_queryset(user))
