"""The continuation helpers' boundaries (QA-01, QA-02, QA-05, QA-06, QA-08, QA-09).

Synthetic graphs far beyond anything real (the largest real component has four
stages): what matters is that the answers stay honest and bounded at any size.

* QA-06 — a reader who may not read the Matter gets nothing from any helper.
* QA-05 — an unreadable stage is a yes/no flag, never a count, on the data
  layer and in MCP `get_matter`.
* QA-08 — the direct predecessors are capped with a «more» flag, and no level
  of the history loads more rows than the bound needs.
* QA-09 — a cycle met exactly at the `MAX_HOPS` bound is a cycle.
* QA-01 / QA-02 — a stage shown as a confirmed continuation is never also
  offered as an uncertain «Võimalik seos», past 120 Matters and 24 hops.
"""

from __future__ import annotations

import json
import re
from itertools import pairwise
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.core.authorization import Scope
from app.core.enums import Visibility
from app.matters.continuations import (
    MAX_HISTORY,
    MAX_HOPS,
    MAX_PREDECESSORS,
    confirmed_continuation_ids,
    continuation_component_ids,
    continuations_for,
    historical_predecessors,
)
from app.matters.models import Matter
from app.mcp_server import readers
from app.related_materials import engine
from app.related_materials.selectors import related_materials_for
from app.workflow.enums import Disposition
from tests import factories

pytestmark = pytest.mark.django_db

_N = [0]


def _teema(owner: Any = None, **fields: Any) -> Matter:
    _N[0] += 1
    fields.setdefault("reference_year", 2031)
    fields.setdefault("reference_number", _N[0])
    if owner is not None:
        fields["owner"] = owner
    return factories.MatterFactory(**fields)


def _continues(earlier: Matter, later: Matter) -> None:
    Matter.all_objects.filter(pk=earlier.pk).update(
        superseded_by=later,
        is_open=False,
        closed_at=timezone.now(),
        disposition=Disposition.SUPERSEDED,
    )


def _converge(many: list[Matter], target: Matter) -> None:
    Matter.all_objects.filter(pk__in=[m.pk for m in many]).update(
        superseded_by=target,
        is_open=False,
        closed_at=timezone.now(),
        disposition=Disposition.SUPERSEDED,
    )


def _fresh(matter: Matter) -> Matter:
    return Matter.all_objects.get(pk=matter.pk)


def _chain(n: int, owner: Any = None) -> list[Matter]:
    stages = [_teema(owner) for _ in range(n)]
    for earlier, later in pairwise(stages):
        _continues(earlier, later)
    return [_fresh(s) for s in stages]


@pytest.fixture(autouse=True)
def public_origin(settings):
    settings.JURISTID_PUBLIC_ORIGIN = "https://juristid.example.test"


@pytest.fixture
def narrow(monkeypatch, specialist):
    """A hand-built partial scope (unreachable from a request under ADR 0181).

    `specialist` reads NORMAL Matters and the ones it takes part in, nothing
    RESTRICTED of anybody else's: the configuration in which «a stage this
    reader cannot open» exists at all.
    """
    from app.core.authorization import scope_for_user as real

    def scope(user: Any) -> Scope:
        if getattr(user, "pk", None) == specialist.pk:
            return Scope(
                user=user,
                is_authenticated=True,
                sees_restricted_by_role=False,
                break_glass_grant_id=None,
            )
        return real(user)

    monkeypatch.setattr("app.matters.models.scope_for_user", scope)
    return specialist


def _secret() -> Matter:
    return _teema(factories.UserFactory(), visibility=Visibility.RESTRICTED)


# ---------------------------------------------------------------------------
# QA-06: nothing at all for a Matter the reader may not read
# ---------------------------------------------------------------------------


def test_every_helper_is_empty_for_a_matter_the_reader_may_not_read(administrator):
    """An outsider holding a primary key learns no flag, stage or id from any helper."""
    first, middle, last = _chain(3)
    hidden_side = _teema()
    _continues(hidden_side, last)
    for viewed in (_fresh(first), _fresh(middle), _fresh(last)):
        chain = continuations_for(viewed, administrator)
        assert chain == type(chain)(), chain
        assert not chain.hidden_predecessors and not chain.hidden_after
        assert not chain.hidden_before and not chain.more_predecessors
        assert historical_predecessors(viewed, administrator).stages == ()
        assert not historical_predecessors(viewed, administrator).truncated
        # Only the unscoped component walk stays: ids for exclusion, never shown.
        assert confirmed_continuation_ids(viewed, administrator) == continuation_component_ids(
            viewed.pk
        )


def test_a_reader_of_the_matter_still_gets_the_flags(narrow):
    secret = _secret()
    viewed = _teema(narrow)
    _continues(secret, viewed)
    chain = continuations_for(_fresh(viewed), narrow)
    assert chain.predecessors == () and chain.hidden_predecessors is True
    assert chain.hidden_before is True


# ---------------------------------------------------------------------------
# QA-05: a yes/no flag, never a count
# ---------------------------------------------------------------------------


def test_hidden_predecessors_is_a_boolean_however_many_are_hidden(narrow):
    viewed = _teema(narrow)
    seen = _teema(narrow)
    _continues(seen, viewed)
    for _ in range(3):
        _continues(_secret(), viewed)

    chain = continuations_for(_fresh(viewed), narrow)

    assert chain.hidden_predecessors is True
    assert [p.pk for p in chain.predecessors] == [seen.pk]


def test_mcp_get_matter_says_yes_or_no_and_never_how_many(narrow):
    viewed = _teema(narrow)
    _continues(_teema(narrow), viewed)
    for _ in range(3):
        _continues(_secret(), viewed)

    c = readers._continuations(narrow, _fresh(viewed))

    assert c["hidden_predecessors"] is True
    for key, value in c.items():
        if key.startswith("hidden") or key.endswith("_hidden"):
            assert isinstance(value, bool), key
    text = readers._predecessor_line(c) or ""
    assert (
        text == f"{c['predecessors'][0]['reference']}, an earlier stage that cannot be opened here"
    )
    assert "may not read" not in text and "(s)" not in text


def test_mcp_names_no_permission_for_a_deleted_successor(specialist):
    old, new = _chain(2, specialist)
    Matter.all_objects.filter(pk=new.pk).update(deleted_at=timezone.now())
    c = readers._continuations(specialist, _fresh(old))
    assert c["successor_hidden"] is True
    assert readers._continuation_line(c) == "a matter that cannot be opened here"


# ---------------------------------------------------------------------------
# QA-08: bounded direct predecessors and bounded level loads
# ---------------------------------------------------------------------------


def test_150_converging_predecessors_give_a_bounded_page_and_payload(client, specialist):
    viewed = _teema(specialist)
    many = [_teema(specialist) for _ in range(150)]
    _converge(many, viewed)
    viewed = _fresh(viewed)

    chain = continuations_for(viewed, specialist)
    assert len(chain.predecessors) == MAX_PREDECESSORS and chain.more_predecessors
    # The nearest by reference, in order.
    assert [p.pk for p in chain.predecessors] == [m.pk for m in many[:MAX_PREDECESSORS]]

    payload = readers.matter_snapshot(specialist, viewed.display_reference)["continuations"]
    assert len(payload["predecessors"]) == MAX_PREDECESSORS
    assert payload["more_predecessors"] is True
    assert len(payload["historical_predecessors"]) == MAX_HISTORY
    assert payload["historical_truncated"] is True
    assert len(json.dumps(payload)) < 20_000

    client.force_login(specialist)
    page = client.get(reverse("matters:matter_detail", kwargs={"pk": viewed.pk}))
    assert page.status_code == 200
    head_links = re.findall(r'<li><a href="/teemad/[0-9a-f-]+/" title=', page.content.decode())
    assert len(head_links) <= MAX_PREDECESSORS


def test_a_few_predecessors_are_all_named_without_more(specialist):
    viewed = _teema(specialist)
    few = [_teema(specialist) for _ in range(3)]
    _converge(few, viewed)
    chain = continuations_for(_fresh(viewed), specialist)
    assert [p.pk for p in chain.predecessors] == [m.pk for m in few]
    assert chain.more_predecessors is False


def test_no_level_query_loads_more_than_the_bound(specialist):
    """`MAX_HISTORY` is enforced at the database, not after loading the whole level."""
    viewed = _teema(specialist)
    _converge([_teema(specialist) for _ in range(150)], viewed)
    viewed = _fresh(viewed)
    with CaptureQueriesContext(connection) as queries:
        history = historical_predecessors(viewed, specialist)
    assert len(history.stages) == MAX_HISTORY and history.truncated
    level_queries = [
        q["sql"] for q in queries.captured_queries if '"superseded_by_id" IN' in q["sql"]
    ]
    assert level_queries
    for sql in level_queries:
        limit = re.search(r"LIMIT (\d+)", sql)
        assert limit is not None and int(limit.group(1)) <= MAX_HISTORY + 1, sql


# ---------------------------------------------------------------------------
# QA-09: a cycle met exactly at the bound
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("length", [MAX_HOPS + 1])
def test_a_cycle_of_max_hops_plus_one_is_reported_as_a_cycle(client, specialist, length):
    stages = [_teema(specialist) for _ in range(length)]
    for earlier, later in zip(stages, stages[1:] + stages[:1], strict=True):
        _continues(earlier, later)
    viewed = _fresh(stages[0])

    history = historical_predecessors(viewed, specialist)

    assert len(history.stages) == MAX_HOPS
    assert history.cyclic is True
    assert history.truncated is False
    client.force_login(specialist)
    fragment = client.get(
        reverse("related_materials:section", kwargs={"pk": viewed.pk}), HTTP_HX_REQUEST="true"
    )
    assert 'data-related-stages-note="cyclic"' in fragment.content.decode()


def test_a_cycle_longer_than_the_bound_is_cut_not_called_complete(specialist):
    stages = [_teema(specialist) for _ in range(MAX_HOPS + 2)]
    for earlier, later in zip(stages, stages[1:] + stages[:1], strict=True):
        _continues(earlier, later)
    history = historical_predecessors(_fresh(stages[0]), specialist)
    assert len(history.stages) == MAX_HOPS
    assert history.truncated is True


def test_a_chain_exactly_at_the_bound_is_complete(specialist):
    stages = _chain(MAX_HOPS + 1)
    history = historical_predecessors(stages[-1], specialist)
    assert len(history.stages) == MAX_HOPS
    assert not history.truncated and not history.cyclic


# ---------------------------------------------------------------------------
# QA-01 / QA-02: a shown stage is never an uncertain suggestion
# ---------------------------------------------------------------------------


def test_shown_history_is_excluded_beyond_120_matters_and_24_hops(specialist):
    """A 30-hop chain whose successor has 130 other predecessors (audit A18/A23)."""
    chain = _chain(30, specialist)
    viewed = chain[24]
    successor = chain[25]
    siblings = [_teema(specialist) for _ in range(MAX_HISTORY * 2 + 10)]
    _converge(siblings, successor)
    viewed = _fresh(viewed)

    shown_back = {s.matter.pk for s in historical_predecessors(viewed, specialist).stages}
    shown_forward = {m.pk for m in continuations_for(viewed, specialist).after}
    assert len(shown_back) == MAX_HOPS and len(shown_forward) == 5
    # The component walk alone stops at its budget (documented, QA-02) ...
    component = continuation_component_ids(viewed.pk)
    assert shown_back - component, "the synthetic case no longer reproduces the audit's cut"
    # ... and the exclusion still holds every shown stage (QA-01).
    excluded = engine.build_profile(viewed, specialist).excluded_matter_ids
    assert shown_back <= excluded and shown_forward <= excluded


def test_a_shown_stage_is_not_also_suggested_under_a_wide_sibling_fan(specialist):
    """End to end (audit A24): «Varasemad etapid» and «Võimalikud seosed» never share a row."""
    stages = [
        _teema(
            specialist,
            title=f"Jäätmeseaduse muutmine, etapp {i + 1}",
            reference_year=2020 + i,
            reference_number=900 + i,
        )
        for i in range(6)
    ]
    for earlier, later in pairwise(stages):
        _continues(earlier, later)
    _p4, _p3, _p2, _p1, viewed, successor = stages
    siblings = [_teema(specialist, title=f"Muu teema {i}") for i in range(MAX_HISTORY * 2 + 5)]
    _converge(siblings, successor)

    shown = {i.matter.pk for i in related_materials_for(_fresh(viewed), specialist).stages}
    suggested = {i.matter.pk for i in engine.suggestions_for(_fresh(viewed), specialist).matters}

    assert len(shown) == 4
    assert not shown & suggested


def test_a_hidden_predecessor_is_flagged_even_when_readable_ones_fill_the_bound(narrow):
    viewed = _teema(narrow)
    _converge([_teema(narrow) for _ in range(MAX_HISTORY + 5)], viewed)
    _continues(_secret(), viewed)
    chain = continuations_for(_fresh(viewed), narrow)
    assert chain.hidden_predecessors is True and chain.more_predecessors is True
    assert len(chain.predecessors) == MAX_PREDECESSORS
    # Without the hidden one the same shape says no.
    plain = _teema(narrow)
    _converge([_teema(narrow) for _ in range(MAX_HISTORY + 5)], plain)
    assert continuations_for(_fresh(plain), narrow).hidden_predecessors is False
