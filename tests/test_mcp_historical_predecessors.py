"""`get_matter` lists every earlier stage of a Teema, through every branch.

`continuations.chain` is the linear part of the history and stops where two
histories converge. `continuations.historical_predecessors` is the whole
earlier history from `app.matters.continuations.historical_predecessors`, in
the same reader's scope: a stage the reader may not read is neither listed nor
counted, and nothing behind it is reached. It is bounded, and says so with
`historical_truncated`. Payload only: the tool's tools/list entry is frozen
(tests/test_mcp_server_write_tools.py, READ_TOOLS_SHA256).
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from django.utils import timezone

from app.matters.continuations import MAX_HISTORY, MAX_HOPS
from app.matters.models import Matter
from app.matters.services import close_matter
from app.mcp_server import readers
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db

ORIGIN = "https://juristid.example.test"


@pytest.fixture(autouse=True)
def public_origin(settings):
    settings.JURISTID_PUBLIC_ORIGIN = ORIGIN


def _matter(owner, title: str, **fields) -> Matter:
    return factories.MatterFactory(owner=owner, title=title, **fields)


def _continues_as(earlier: Matter, later: Matter) -> None:
    Matter.all_objects.filter(pk=earlier.pk).update(superseded_by=later)


def _chain(n: int, owner) -> list[Matter]:
    matters = [_matter(owner, f"Etapp {index}") for index in range(1, n + 1)]
    for earlier, later in pairwise(matters):
        _continues_as(earlier, later)
    return [Matter.all_objects.get(pk=m.pk) for m in matters]


def _continuations(viewer, matter: Matter) -> dict:
    return readers.matter_snapshot(viewer, matter.display_reference)["continuations"]


def _text(viewer, matter: Matter) -> str:
    return readers.fetch_source(viewer, f"matter:{matter.pk}")["text"]


def test_a_converging_history_lists_every_branch_with_its_depth(specialist):
    """older → first ─┐
             ├→ merged → viewed
    second ──┘
    """
    older = _matter(specialist, "Algne")
    first = _matter(specialist, "Esimene haru")
    second = _matter(specialist, "Teine haru")
    merged = _matter(specialist, "Ühendatud")
    viewed = _matter(specialist, "Vaadatav")
    _continues_as(older, first)
    close_matter(
        matter=first, disposition=Disposition.SUPERSEDED, actor=specialist, successor=merged
    )
    _continues_as(second, merged)
    _continues_as(merged, viewed)

    c = _continuations(specialist, viewed)
    history = c["historical_predecessors"]

    # Furthest back first, then by reference.
    branches = sorted(
        (first, second), key=lambda m: (m.reference_year or 0, m.reference_number or 0, str(m.pk))
    )
    assert [item["id"] for item in history] == [
        f"matter:{older.pk}",
        *(f"matter:{m.pk}" for m in branches),
        f"matter:{merged.pk}",
    ]
    by_id = {item["id"]: item for item in history}
    assert by_id[f"matter:{merged.pk}"]["depth"] == 1
    assert by_id[f"matter:{first.pk}"]["depth"] == 2
    assert by_id[f"matter:{second.pk}"]["depth"] == 2
    assert by_id[f"matter:{older.pk}"]["depth"] == 3
    assert by_id[f"matter:{first.pk}"]["is_open"] is False
    assert by_id[f"matter:{second.pk}"]["is_open"] is True
    assert by_id[f"matter:{older.pk}"] == {
        "id": f"matter:{older.pk}",
        "reference": older.display_reference,
        "url": f"{ORIGIN}/teemad/{older.pk}/",
        "depth": 3,
        "is_open": True,
    }
    assert c["historical_truncated"] is False

    # The linear chain stops at the converging step; the history does not.
    assert [s["id"] for s in c["chain"]] == [f"matter:{merged.pk}", f"matter:{viewed.pk}"]
    text = _text(specialist, viewed)
    assert "Earlier stages (all branches): " in text
    assert f"{older.display_reference} (3 back)" in text
    assert f"{second.display_reference} (2 back)" in text


def test_a_linear_chain_lists_the_same_stages_and_adds_no_text_line(specialist):
    first, middle, last = _chain(3, specialist)

    c = _continuations(specialist, last)

    assert [(item["reference"], item["depth"]) for item in c["historical_predecessors"]] == [
        (first.display_reference, 2),
        (middle.display_reference, 1),
    ]
    assert c["historical_truncated"] is False
    assert "Earlier stages" not in _text(specialist, last)  # «Chain» already says it
    assert _continuations(specialist, first)["historical_predecessors"] == []


def test_a_stage_the_reader_may_not_read_is_not_listed_and_nothing_behind_it_is_reached(
    specialist,
):
    first, middle, last = _chain(3, specialist)
    side = _matter(specialist, "Kõrvalharu")
    _continues_as(side, last)
    Matter.all_objects.filter(pk=middle.pk).update(deleted_at=timezone.now())

    c = _continuations(specialist, last)

    assert [item["id"] for item in c["historical_predecessors"]] == [f"matter:{side.pk}"]
    assert middle.display_reference not in repr(c)
    assert first.display_reference not in repr(c)
    assert middle.display_reference not in _text(specialist, last)


def test_a_history_deeper_than_the_walk_is_cut_and_says_so(specialist):
    chain = _chain(MAX_HOPS + 2, specialist)

    c = _continuations(specialist, chain[-1])

    assert len(c["historical_predecessors"]) == MAX_HOPS
    assert {item["depth"] for item in c["historical_predecessors"]} == set(range(1, MAX_HOPS + 1))
    assert f"matter:{chain[0].pk}" not in repr(c["historical_predecessors"])
    assert c["historical_truncated"] is True
    assert "(more earlier stages than shown)" in _text(specialist, chain[-1])


def test_a_history_wider_than_the_bound_is_cut_and_says_so(specialist):
    viewed = _matter(specialist, "Vaadatav")
    for index in range(MAX_HISTORY + 1):
        _continues_as(_matter(specialist, f"Eelnev {index}"), viewed)

    c = _continuations(specialist, viewed)

    assert len(c["historical_predecessors"]) == MAX_HISTORY
    assert c["historical_truncated"] is True


def test_a_cycle_lists_each_stage_once_and_ends(specialist):
    a, b = _chain(2, specialist)
    _continues_as(b, a)

    c = _continuations(specialist, a)

    assert [item["id"] for item in c["historical_predecessors"]] == [f"matter:{b.pk}"]
    assert c["cyclic"] is True and c["historical_truncated"] is False
