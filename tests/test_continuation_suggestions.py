"""«Võimalikud seosed» never offers a stage of the Teema's own continuation history.

`Matter.superseded_by` is the authoritative continuation relationship
(docs/adr/0207). A stage of it — however many steps away, and through whichever
branch of a converging history — is already established, so the engine must
not present it again as an uncertain match (docs/adr/0062 §4). A Matter that is
merely similar, and not on the history, is still suggested.

Every title below names the same act («Jäätmeseadus»), which on its own is a
strong signal: without the exclusion, every Matter here would suggest every
other one. The first test holds that, so the later ones prove the exclusion
and not a weak corpus.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any

import pytest

from app.matters.models import Matter
from app.related_materials import engine
from tests import factories

pytestmark = pytest.mark.django_db


def _matter(owner: Any, title: str, *, number: int, year: int = 2026) -> Matter:
    return factories.MatterFactory(
        owner=owner, title=title, reference_year=year, reference_number=number
    )


def _continues_as(earlier: Matter, later: Matter) -> None:
    Matter.objects.filter(pk=earlier.pk).update(superseded_by=later)


def _suggested(matter: Matter, viewer: Any) -> set[Any]:
    matter = Matter.objects.get(pk=matter.pk)
    return {item.matter.pk for item in engine.suggestions_for(matter, viewer).matters}


def _chain(owner: Any, length: int, base: int) -> list[Matter]:
    stages = [
        _matter(owner, f"Jäätmeseaduse muutmine, etapp {i + 1}", number=base + i, year=2020 + i)
        for i in range(length)
    ]
    for earlier, later in pairwise(stages):
        _continues_as(earlier, later)
    return stages


def test_without_a_continuation_every_such_matter_suggests_the_others(specialist):
    """The control: these titles are similar enough to be suggested to each other."""
    stages = [
        _matter(specialist, f"Jäätmeseaduse muutmine, etapp {i + 1}", number=700 + i, year=2020 + i)
        for i in range(4)
    ]

    for stage in stages:
        assert _suggested(stage, specialist) == {s.pk for s in stages if s.pk != stage.pk}


@pytest.mark.parametrize("length", [3, 4])
def test_no_stage_of_a_linear_chain_is_suggested_for_any_stage(specialist, length):
    stages = _chain(specialist, length, base=710)
    unrelated = _matter(specialist, "Jäätmeseaduse muutmise eelnõu kooskõlastus", number=790)

    for stage in stages:
        suggested = _suggested(stage, specialist)
        assert suggested.isdisjoint({s.pk for s in stages}), (stage.title, suggested)
        assert unrelated.pk in suggested, stage.title

    # The unrelated Matter still sees the whole chain as candidates.
    assert {s.pk for s in stages} <= _suggested(unrelated, specialist)


def test_no_stage_of_a_converging_history_is_suggested_for_any_stage(specialist):
    """Two branches meet and continue: every stage is on one history, seen from anywhere.

    older → first_branch ─┐
                          ├→ merged → latest
           second_branch ─┘

    From `second_branch`, `older` is neither ancestor nor descendant — it is the
    other branch's earlier stage — and is still the same history.
    """
    older = _matter(specialist, "Jäätmeseaduse muutmine, algne", number=801, year=2021)
    first_branch = _matter(
        specialist, "Jäätmeseaduse muutmine, esimene haru", number=802, year=2022
    )
    second_branch = _matter(specialist, "Jäätmeseaduse muutmine, teine haru", number=803, year=2022)
    merged = _matter(specialist, "Jäätmeseaduse muutmine, ühendatud", number=804, year=2023)
    latest = _matter(specialist, "Jäätmeseaduse muutmine, viimane", number=805, year=2024)
    _continues_as(older, first_branch)
    _continues_as(first_branch, merged)
    _continues_as(second_branch, merged)
    _continues_as(merged, latest)
    unrelated = _matter(specialist, "Jäätmeseaduse muutmise eelnõu kooskõlastus", number=890)
    history = [older, first_branch, second_branch, merged, latest]

    for stage in history:
        suggested = _suggested(stage, specialist)
        assert suggested.isdisjoint({s.pk for s in history}), (stage.title, suggested)
        assert unrelated.pk in suggested, stage.title


def test_a_cycle_in_the_history_still_ends_and_excludes_its_stages(specialist):
    """The database forbids only a self-reference; the exclusion walk must still end."""
    first, second, third = _chain(specialist, 3, base=900)
    _continues_as(third, first)
    unrelated = _matter(specialist, "Jäätmeseaduse muutmise eelnõu kooskõlastus", number=990)

    for stage in (first, second, third):
        suggested = _suggested(stage, specialist)
        assert suggested.isdisjoint({first.pk, second.pk, third.pk})
        assert unrelated.pk in suggested
