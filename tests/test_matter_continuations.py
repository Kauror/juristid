"""The continuation chain a reader may follow (app/matters/continuations.py, docs/adr/0207)."""

from __future__ import annotations

from itertools import pairwise

import pytest
from django.utils import timezone

from app.matters.continuations import MAX_HOPS, continuations_for
from app.matters.models import Matter
from tests import factories

pytestmark = pytest.mark.django_db


def _chain(n: int) -> list[Matter]:
    """n Matters, each continuing as the next."""
    matters = [factories.MatterFactory() for _ in range(n)]
    for earlier, later in pairwise(matters):
        Matter.all_objects.filter(pk=earlier.pk).update(superseded_by=later)
    return [Matter.all_objects.get(pk=m.pk) for m in matters]


def test_a_matter_without_continuations_is_empty(specialist):
    assert not continuations_for(factories.MatterFactory(), specialist)


def test_one_continuation_names_both_ends(specialist):
    old, new = _chain(2)
    forward = continuations_for(old, specialist)
    assert forward.successor == new and forward.after == (new,) and forward.endpoint == new
    assert not forward.has_chain
    back = continuations_for(new, specialist)
    assert back.predecessors == (old,) and back.before == (old,) and back.successor is None


def test_a_three_stage_chain_is_walked_by_direction_not_by_reference(specialist):
    first, middle, last = _chain(3)
    # The newest reference is the *first* stage: direction decides the endpoint.
    Matter.all_objects.filter(pk=first.pk).update(reference_year=2030, reference_number=1)
    c = continuations_for(Matter.all_objects.get(pk=first.pk), specialist)
    assert c.after == (middle, last) and c.endpoint == last and c.has_chain
    assert continuations_for(last, specialist).before == (first, middle)
    mid = continuations_for(middle, specialist)
    assert mid.before == (first,) and mid.after == (last,)


def test_converging_predecessors_are_all_listed_and_end_the_linear_history(specialist):
    a, b, target = factories.MatterFactory(), factories.MatterFactory(), factories.MatterFactory()
    Matter.all_objects.filter(pk__in=[a.pk, b.pk]).update(superseded_by=target)
    c = continuations_for(target, specialist)
    assert set(c.predecessors) == {a, b} and c.before == ()


def test_a_step_the_reader_may_not_read_stops_the_walk_unnamed(specialist):
    first, middle, last = _chain(3)
    Matter.all_objects.filter(pk=middle.pk).update(deleted_at=timezone.now())
    forward = continuations_for(first, specialist)
    assert forward.after == () and forward.hidden_after and forward.successor is None
    back = continuations_for(last, specialist)
    assert back.predecessors == () and back.hidden_predecessors == 0  # a deleted one is not history
    assert not any(m.pk == middle.pk for m in forward.after + back.before)


def test_an_outsider_reads_no_step_at_all(administrator):
    # An outsider may not read the Matter itself, so it learns nothing — not even
    # that a step exists (QA-06; this test asserted `hidden_after` before).
    old, _new = _chain(2)
    c = continuations_for(old, administrator)
    assert c.after == () and c.successor is None and not c.hidden_after
    assert c == type(c)()


def test_a_hidden_predecessor_is_flagged_never_named(specialist, monkeypatch):
    """A reader of the successor who may not read the predecessor: a flag, no count.

    A hand-built scope (unreachable from a request under docs/adr/0181), because
    since QA-06 an outsider who may not read the successor gets nothing at all,
    and since QA-05 the flag is yes/no.
    """
    from app.core.authorization import Scope
    from app.core.authorization import scope_for_user as real
    from app.core.enums import Visibility

    def scope(user):
        if getattr(user, "pk", None) == specialist.pk:
            return Scope(
                user=user,
                is_authenticated=True,
                sees_restricted_by_role=False,
                break_glass_grant_id=None,
            )
        return real(user)

    monkeypatch.setattr("app.matters.models.scope_for_user", scope)
    old, new = _chain(2)
    Matter.all_objects.filter(pk=old.pk).update(
        visibility=Visibility.RESTRICTED, owner=factories.UserFactory()
    )
    Matter.all_objects.filter(pk=new.pk).update(owner=specialist)
    c = continuations_for(Matter.all_objects.get(pk=new.pk), specialist)
    assert c.predecessors == () and c.hidden_predecessors is True and c.hidden_before


def test_a_cycle_ends_the_walk_instead_of_looping(specialist):
    a, b = _chain(2)
    Matter.all_objects.filter(pk=b.pk).update(superseded_by=a)
    c = continuations_for(Matter.all_objects.get(pk=a.pk), specialist)
    assert c.cyclic and c.after == (b,)


def test_a_chain_longer_than_the_bound_is_truncated(specialist):
    chain = _chain(MAX_HOPS + 3)
    c = continuations_for(chain[0], specialist)
    assert c.truncated and len(c.after) == MAX_HOPS
    back = continuations_for(chain[-1], specialist)
    assert back.truncated and len(back.before) == MAX_HOPS


def test_the_walk_is_a_bounded_number_of_queries(specialist, django_assert_max_num_queries):
    chain = _chain(4)
    with django_assert_max_num_queries(2 * (MAX_HOPS + 2)):
        continuations_for(chain[1], specialist)
