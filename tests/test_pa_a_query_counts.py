"""One backward walk per Teema page, one query per level (QA-10).

The header (`continuations_for`), «Seotud materjalid» (`historical_predecessors`)
and the suggestion exclusion each walked the same earlier history, and
`continuations_for` spent two queries per level (every live predecessor's id,
then the readable ones). The walk is now one id-only query per level — every
live predecessor marked readable or not in the same query — plus one row read
for all the stages, done once per Matter instance and reader.

The page numbers are measured on the Teema detail view and the «Seotud
materjalid» fragment for the shapes the audit measured (printed, and appended to
``$PA_A_QUERY_OUT`` when set). What is asserted is relative, so another round's
change to the rest of the page does not move it: a history costs the page one
query per level plus two, however many surfaces show it.
"""

from __future__ import annotations

import os
from itertools import pairwise
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.matters.continuations import (
    MAX_HOPS,
    continuations_for,
    historical_predecessors,
)
from app.matters.models import Matter
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}
_N = [0]


def _teema(owner: Any) -> Matter:
    _N[0] += 1
    return factories.MatterFactory(
        owner=owner, reference_year=2032, reference_number=_N[0], title=f"Mõõdetav {_N[0]}"
    )


def _continues(earlier: Matter, later: Matter) -> None:
    Matter.all_objects.filter(pk=earlier.pk).update(
        superseded_by=later,
        is_open=False,
        closed_at=timezone.now(),
        disposition=Disposition.SUPERSEDED,
    )


def _fresh(matter: Matter) -> Matter:
    return Matter.all_objects.get(pk=matter.pk)


def _chain(n: int, owner: Any) -> list[Matter]:
    stages = [_teema(owner) for _ in range(n)]
    for earlier, later in pairwise(stages):
        _continues(earlier, later)
    return [_fresh(s) for s in stages]


def _scenario(name: str, owner: Any) -> Matter:
    if name == "none":
        return _teema(owner)
    if name == "direct":
        return _chain(2, owner)[-1]
    if name == "four_stages":
        return _chain(4, owner)[-1]
    if name == "converging":
        viewed = _teema(owner)
        for _ in range(3):
            _continues(_teema(owner), viewed)
        return _fresh(viewed)
    if name == "deep30":
        return _chain(30, owner)[-1]
    raise AssertionError(name)


def _count(call: Any) -> int:
    with CaptureQueriesContext(connection) as queries:
        call()
    return len(queries.captured_queries)


SCENARIOS = ("none", "direct", "four_stages", "converging", "deep30")


@pytest.mark.parametrize("name", SCENARIOS)
def test_page_and_fragment_query_counts(client, specialist, name):
    viewed = _scenario(name, specialist)
    client.force_login(specialist)
    detail = reverse("matters:matter_detail", kwargs={"pk": viewed.pk})
    section = reverse("related_materials:section", kwargs={"pk": viewed.pk})
    client.get(detail)  # warm: session, persona, compiled templates

    page = _count(lambda: client.get(detail))
    fragment = _count(lambda: client.get(section, **HX))
    suggestions = _count(lambda: client.get(section + "?avatud=1", **HX))
    line = f"QA-10 {name}: page={page} fragment={fragment} suggestions={suggestions}"
    print(line)
    out = os.environ.get("PA_A_QUERY_OUT")
    if out:
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def test_the_page_pays_for_its_history_once(client, specialist):
    client.force_login(specialist)

    def page(viewed: Matter) -> int:
        url = reverse("matters:matter_detail", kwargs={"pk": viewed.pk})
        client.get(url)
        return _count(lambda: client.get(url))

    baseline = page(_scenario("none", specialist))
    # The readability check and the empty first level are in the baseline; a
    # history adds its other levels and one row read — once, for the header,
    # the rail and «Seotud materjalid» together.
    assert page(_scenario("four_stages", specialist)) - baseline <= 4
    assert page(_scenario("deep30", specialist)) - baseline <= MAX_HOPS + 1


@pytest.mark.parametrize(
    ("stages", "levels"),
    [(1, 1), (2, 2), (4, 4), (MAX_HOPS + 1, MAX_HOPS + 1), (30, MAX_HOPS + 1)],
)
def test_the_backward_walk_is_one_query_per_level_and_runs_once(specialist, stages, levels):
    viewed = _chain(stages, specialist)[-1]

    first = _count(lambda: continuations_for(viewed, specialist))
    second = _count(lambda: historical_predecessors(viewed, specialist))

    # The readability check, one query per level, and one row read when there
    # are stages: never two per level.
    assert first == 1 + levels + (1 if stages > 1 else 0), first
    # The same instance and reader: the history is not walked again.
    assert second == 0


def test_the_memo_is_per_reader_and_follows_this_matters_own_writes(specialist, administrator):
    old, viewed = _chain(2, specialist)
    assert historical_predecessors(viewed, specialist).stages[0].matter == old
    # Another reader is asked from scratch, and gets its own (empty) answer.
    assert historical_predecessors(viewed, administrator).stages == ()
    assert continuations_for(viewed, administrator).predecessors == ()
    # Same reader again: still its own answer.
    assert continuations_for(viewed, specialist).predecessors == (old,)
    # A write to this Matter's own chain columns is a new key.
    later = _teema(specialist)
    viewed.superseded_by = later
    viewed.is_open = False
    assert _count(lambda: historical_predecessors(viewed, specialist)) > 0
    # Another instance of the same Matter walks for itself.
    assert _count(lambda: historical_predecessors(_fresh(viewed), specialist)) > 0
