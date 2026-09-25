"""Which build of the search projection readers read, and which ones writers write.

A full rebuild writes a new generation of rows beside the active one instead of
emptying and refilling the table under a lock every business write has to wait
for (ENG-011, docs/adr/0118). Two rules keep that correct, and they live here so
nothing else has to restate them:

* **Readers read the active generation, and only it.** `projection()` is the
  table as readers see it; `app.search.services.visible_documents`, the
  integrity check and the index-state probe all start from it. The active
  number is an uncorrelated scalar subquery, so it is evaluated once per
  statement and a statement sees one generation from start to finish — the
  swap is atomic to every reader.
* **Writers write every live generation.** A targeted refresh replaces its
  source's rows in the active generation and, while a rebuild is filling one,
  in the building generation too (`live_generations()`), so a change committed
  during a rebuild is in the generation that becomes active. It reads the live
  set after taking the shared side of the refresh gate
  (`app.search.indexing._hold_off_a_rebuild`), which a rebuild's exclusive side
  orders against: a generation cannot appear or be activated between the moment
  a writer learns the set and the moment it commits.

When no generation is recorded as active — a database that has never been
rebuilt since generations existed, or one whose bookkeeping was lost — the
active generation is `INITIAL_GENERATION`, which is where such a database's rows
are.
"""

from __future__ import annotations

from django.db.models import IntegerField, QuerySet, Subquery, Value
from django.db.models.functions import Coalesce

from app.search.models import (
    INITIAL_GENERATION,
    SearchDocument,
    SearchGeneration,
    SearchGenerationState,
)


def active_generation_expression() -> Coalesce:
    """The active generation's number, as SQL: one scalar subquery."""
    return Coalesce(
        Subquery(
            # At most one ACTIVE row exists (a partial unique index), so no
            # ordering is needed; `Meta.ordering` would add a sort for nothing.
            SearchGeneration.objects.filter(state=SearchGenerationState.ACTIVE)
            .order_by()
            .values("number")[:1]
        ),
        Value(INITIAL_GENERATION),
        output_field=IntegerField(),
    )


def active_generation() -> int:
    number = (
        SearchGeneration.objects.filter(state=SearchGenerationState.ACTIVE)
        .values_list("number", flat=True)
        .first()
    )
    return number if number is not None else INITIAL_GENERATION


def building_generation() -> int | None:
    return (
        SearchGeneration.objects.filter(state=SearchGenerationState.BUILDING)
        .values_list("number", flat=True)
        .first()
    )


def live_generations() -> list[int]:
    """The generations a targeted refresh must write: active, and building if any.

    Call only while holding the shared side of the refresh gate, or the answer
    can change before the write that relies on it commits.
    """
    live = [active_generation()]
    building = building_generation()
    if building is not None and building not in live:
        live.append(building)
    return live


def projection() -> QuerySet[SearchDocument]:
    """The projection as every reader sees it: the active generation's rows."""
    return SearchDocument.objects.filter(generation=active_generation_expression())
