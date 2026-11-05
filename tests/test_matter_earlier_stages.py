"""Every earlier stage of a Teema, through every branch (app/matters/continuations.py, ADR 0209)."""

from __future__ import annotations

from itertools import pairwise

import pytest
from django.utils import timezone

from app.matters.continuations import (
    MAX_HOPS,
    continuation_component_ids,
    historical_predecessors,
)
from app.matters.models import Matter
from tests import factories

pytestmark = pytest.mark.django_db


def _chain(*refs: str) -> list[Matter]:
    matters = []
    for ref in refs:
        year, number = (int(x) for x in ref.split("_"))
        matters.append(factories.MatterFactory(reference_year=year, reference_number=number))
    for earlier, later in pairwise(matters):
        Matter.all_objects.filter(pk=earlier.pk).update(superseded_by=later)
    return [Matter.all_objects.get(pk=m.pk) for m in matters]


def _refs(stages) -> list[str]:
    return [s.matter.display_reference for s in stages.stages]


def test_a_teema_without_history_has_no_earlier_stages(specialist):
    assert not historical_predecessors(factories.MatterFactory(), specialist)


def test_the_endpoint_of_a_three_stage_chain_lists_both_earlier_stages(specialist):
    _first, middle, last = _chain("2025_10", "2026_55", "2026_190")
    stages = historical_predecessors(last, specialist)
    assert _refs(stages) == ["2025_10", "2026_55"]  # earliest first
    assert [s.depth for s in stages.stages] == [2, 1]
    assert stages.stages[0].continues_as == middle.pk


def test_converging_predecessors_are_all_listed(specialist):
    _a, target = _chain("2022_74", "2026_149")
    b = factories.MatterFactory(reference_year=2026, reference_number=88)
    Matter.all_objects.filter(pk=b.pk).update(superseded_by=target)
    assert sorted(_refs(historical_predecessors(target, specialist))) == ["2022_74", "2026_88"]


def test_every_branch_is_followed_to_its_start(specialist):
    _a1, _a2, target = _chain("2020_1", "2021_2", "2026_9")
    _b1, b2 = _chain("2019_5", "2023_6")
    Matter.all_objects.filter(pk=b2.pk).update(superseded_by=target)
    assert sorted(_refs(historical_predecessors(target, specialist))) == [
        "2019_5",
        "2020_1",
        "2021_2",
        "2023_6",
    ]


def test_a_four_stage_chain_lists_every_earlier_stage(specialist):
    chain = _chain("2023_1", "2024_2", "2025_3", "2026_4")
    assert _refs(historical_predecessors(chain[-1], specialist)) == ["2023_1", "2024_2", "2025_3"]


def test_a_deleted_stage_is_not_shown_and_not_walked_through(specialist):
    _first, middle, last = _chain("2024_1", "2025_2", "2026_3")
    Matter.all_objects.filter(pk=middle.pk).update(deleted_at=timezone.now())
    assert _refs(historical_predecessors(last, specialist)) == []


def test_an_outsider_reads_no_stage_and_no_count(administrator):
    _first, last = _chain("2024_1", "2026_2")
    stages = historical_predecessors(last, administrator)
    assert stages.stages == () and not stages.cyclic and not stages.truncated


def test_a_cycle_ends_the_walk(specialist):
    a, b = _chain("2025_1", "2026_2")
    Matter.all_objects.filter(pk=b.pk).update(superseded_by=a)
    stages = historical_predecessors(Matter.all_objects.get(pk=b.pk), specialist)
    assert _refs(stages) == ["2025_1"]
    assert stages.cyclic


def test_a_history_deeper_than_the_bound_is_cut_and_says_so(specialist):
    refs = [f"{2000 + n}_{n + 1}" for n in range(MAX_HOPS + 3)]
    chain = _chain(*refs)
    stages = historical_predecessors(chain[-1], specialist)
    assert len(stages) == MAX_HOPS and stages.truncated


def test_one_query_per_level(specialist, django_assert_max_num_queries):
    chain = _chain("2022_1", "2023_2", "2024_3", "2025_4", "2026_5")
    with django_assert_max_num_queries(len(chain) + 1):
        historical_predecessors(chain[-1], specialist)


def test_the_component_holds_every_stage_in_both_directions(specialist):
    first, middle, last = _chain("2024_1", "2025_2", "2026_3")
    side = factories.MatterFactory()
    Matter.all_objects.filter(pk=side.pk).update(superseded_by=middle)
    ids = continuation_component_ids(first.pk)
    assert ids == {first.pk, middle.pk, last.pk, side.pk}
    alone = factories.MatterFactory()
    assert continuation_component_ids(alone.pk) == {alone.pk}


def test_a_history_exactly_at_the_bound_is_not_called_truncated(specialist):
    refs = [f"{2000 + n}_{n + 1}" for n in range(MAX_HOPS + 1)]
    chain = _chain(*refs)
    stages = historical_predecessors(chain[-1], specialist)
    assert len(stages) == MAX_HOPS and not stages.truncated
