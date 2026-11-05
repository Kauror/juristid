"""Agent E (independent verifier): the one backward walk and its bounds.

QA-01/02 (exclusion), QA-08 (direct-predecessor cap), QA-09 (cycle at the
bound), QA-10 (memo), QA-11 (Dokumendid follows every branch), QA-12 (marks).
Written against the audit's statements and the owner's decisions, not the
authors' tests. Synthetic data only.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters.continuations import (
    MAX_HISTORY,
    MAX_HOPS,
    MAX_PREDECESSORS,
    confirmed_continuation_ids,
    continuations_for,
    historical_predecessors,
)
from app.matters.models import Matter
from app.matters.services import close_matter
from app.mcp_server import readers
from app.related_materials import engine
from app.related_materials.selectors import related_materials_for
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}
_N = [0]


@pytest.fixture(autouse=True)
def public_origin(settings):
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example.test"


def _teema(owner: Any, **fields: Any) -> Matter:
    _N[0] += 1
    fields.setdefault("reference_year", 2033)
    fields.setdefault("reference_number", _N[0])
    fields.setdefault("title", f"PAE ajalugu {_N[0]}")
    return factories.MatterFactory(owner=owner, **fields)


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


# -- QA-10: the memo ---------------------------------------------------------------


def test_e_memo_follows_this_matters_own_close_and_reopen(specialist):
    p, m, s = [*_chain(2, specialist), _teema(specialist)]
    instance = _fresh(m)
    assert [x.matter.pk for x in historical_predecessors(instance, specialist).stages] == [p.pk]
    closed = close_matter(
        matter=instance, disposition=Disposition.SUPERSEDED, actor=specialist, successor=s
    )
    # close_matter wrote through a locked copy; the page re-renders the copy.
    assert continuations_for(closed, specialist).successor.pk == s.pk
    assert [x.matter.pk for x in historical_predecessors(closed, specialist).stages] == [p.pk]


def test_e_memo_is_stale_after_a_write_to_another_matter_on_the_same_instance(specialist):
    """The documented limit (QA-10 remaining risk), pinned so it cannot widen.

    A predecessor closed *as this Matter* in the same unit of work is not seen
    by an instance that already walked its history; a fresh instance sees it.
    No current request does this: `close_matter` (the only runtime writer of
    `superseded_by`) is never reached with a successor from a view, and every
    reader surface re-reads its Matter per request.
    """
    m = _teema(specialist)
    p = _teema(specialist)
    instance = _fresh(m)
    assert historical_predecessors(instance, specialist).stages == ()
    close_matter(
        matter=_fresh(p), disposition=Disposition.SUPERSEDED, actor=specialist, successor=_fresh(m)
    )
    assert historical_predecessors(instance, specialist).stages == ()  # stale, by design
    assert continuations_for(instance, specialist).predecessors == ()  # stale, by design
    fresh = _fresh(m)
    assert [x.matter.pk for x in historical_predecessors(fresh, specialist).stages] == [p.pk]
    assert [x.pk for x in continuations_for(fresh, specialist).predecessors] == [p.pk]


def test_e_one_page_request_walks_once_and_fragments_are_fresh(client, specialist):
    """Each HTMX request is a new instance: after another request closes a new
    predecessor into the viewed Teema, the next fragment shows it."""
    m = _teema(specialist)
    client.force_login(specialist)
    section = reverse("related_materials:section", kwargs={"pk": m.pk})
    assert "PAE myöhem" not in client.get(section, **HX).content.decode()
    p = _teema(specialist, title="PAE myöhem eelmine")
    close_matter(
        matter=_fresh(p), disposition=Disposition.SUPERSEDED, actor=specialist, successor=_fresh(m)
    )
    body = client.get(section, **HX).content.decode()
    assert "PAE myöhem eelmine" in body
    assert "vahetu eelmine etapp" in body


# -- QA-08 -------------------------------------------------------------------------


def test_e_direct_predecessor_cap_header_mcp_and_section_agree(client, specialist):
    viewed = _teema(specialist)
    for _ in range(MAX_PREDECESSORS + 3):
        _continues(_teema(specialist), viewed)
    chain = continuations_for(_fresh(viewed), specialist)
    assert len(chain.predecessors) == MAX_PREDECESSORS and chain.more_predecessors
    block = readers._continuations(specialist, _fresh(viewed))
    assert len(block["predecessors"]) == MAX_PREDECESSORS and block["more_predecessors"]
    assert "more (see the earlier stages)" in readers._predecessor_line(block)
    assert len(block["historical_predecessors"]) == MAX_PREDECESSORS + 3
    client.force_login(specialist)
    body = client.get(reverse("matters:matter_detail", kwargs={"pk": viewed.pk})).content.decode()
    assert "ja veel, vt «Varasemad etapid»" in body
    stages = related_materials_for(_fresh(viewed), specialist).stages
    assert len(stages) == MAX_PREDECESSORS + 3


def test_e_exactly_the_cap_has_no_more_flag(specialist):
    viewed = _teema(specialist)
    for _ in range(MAX_PREDECESSORS):
        _continues(_teema(specialist), viewed)
    chain = continuations_for(_fresh(viewed), specialist)
    assert len(chain.predecessors) == MAX_PREDECESSORS and not chain.more_predecessors


def test_e_history_cut_at_max_history_is_truncated_and_never_cyclic(specialist):
    viewed = _teema(specialist)
    for _ in range(MAX_HISTORY + 5):
        _continues(_teema(specialist), viewed)
    history = historical_predecessors(_fresh(viewed), specialist)
    assert len(history) == MAX_HISTORY and history.truncated and not history.cyclic


# -- QA-09 -------------------------------------------------------------------------


@pytest.mark.parametrize("length", [MAX_HOPS, MAX_HOPS + 1, MAX_HOPS + 2])
def test_e_cycles_around_the_bound_are_never_reported_as_complete(specialist, length):
    stages = [_teema(specialist) for _ in range(length)]
    for earlier, later in pairwise([*stages, stages[0]]):
        Matter.all_objects.filter(pk=earlier.pk).update(superseded_by=later)
    history = historical_predecessors(_fresh(stages[0]), specialist)
    assert history.cyclic or history.truncated, length
    if length <= MAX_HOPS + 1:
        assert history.cyclic, length
    chain = continuations_for(_fresh(stages[0]), specialist)
    assert chain.cyclic or chain.truncated


def test_e_a_chain_one_beyond_the_bound_is_truncated_not_cyclic(specialist):
    stages = _chain(MAX_HOPS + 2, specialist)
    history = historical_predecessors(stages[-1], specialist)
    assert history.truncated and not history.cyclic and len(history) == MAX_HOPS


# -- QA-01 / QA-02 -----------------------------------------------------------------


def test_e_every_shown_stage_is_excluded_on_any_shape(specialist):
    """Converging, deep and wide at once; the shown set and the forward chain
    are inside the exclusion whatever the component walk's budget reaches."""
    viewed = _teema(specialist)
    deep = _chain(MAX_HOPS + 4, specialist)
    _continues(deep[-1], viewed)
    for _ in range(MAX_HISTORY):
        _continues(_teema(specialist), viewed)
    later = _chain(5, specialist)
    _continues(viewed, later[0])
    for _ in range(MAX_HISTORY * 2):
        _continues(_teema(specialist), later[2])  # a wide fan of siblings ahead
    instance = _fresh(viewed)
    shown = {s.matter.pk for s in historical_predecessors(instance, specialist).stages}
    ahead = {s.pk for s in continuations_for(instance, specialist).after}
    excluded = confirmed_continuation_ids(instance, specialist)
    assert shown and ahead
    assert shown <= excluded and ahead <= excluded
    offered = {
        item.matter.pk
        for item in engine.suggestions_for(_fresh(viewed), specialist, limit=50).matters
    }
    assert not offered & (shown | ahead)


# -- QA-12 -------------------------------------------------------------------------


def test_e_every_deeper_row_names_a_listed_stage_and_direct_rows_are_marked(client, specialist):
    """a1 -> a2 -> T, b1 -> b2 -> b3 -> T, c1 -> T: each deeper row's «jätkus
    teemana X» names the row it continued as, and X is listed or is T."""
    t = _teema(specialist)
    a = _chain(2, specialist)
    b = _chain(3, specialist)
    c1 = _teema(specialist)
    _continues(a[-1], t)
    _continues(b[-1], t)
    _continues(c1, t)
    items = related_materials_for(_fresh(t), specialist).stages
    listed = {item.matter.pk for item in items} | {t.pk}
    for item in items:
        if item.depth == 1:
            assert item.is_direct and item.continued_as_label == ""
        else:
            assert not item.is_direct
            assert item.continued_as is not None and item.continued_as.pk in listed
            assert item.continued_as_label == item.continued_as.display_reference
            assert item.continued_as.pk == item.matter.superseded_by_id
    client.force_login(specialist)
    body = client.get(reverse("related_materials:section", kwargs={"pk": t.pk}), **HX)
    html = body.content.decode()
    assert html.count("vahetu eelmine etapp") == 3
    assert html.count("jätkus teemana") == 3
