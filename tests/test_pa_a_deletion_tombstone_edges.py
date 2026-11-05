"""A deleted predecessor no longer blocks deleting its successor (QA-20, docs/adr/0102).

`delete_matter` refuses to delete a Matter that another Matter names as its
successor (`BLOCKED_BY_RELATED_MATTER`): deleting it would leave a live record
pointing at a tombstone. A *deleted* predecessor is not such a record. Its
tombstone keeps `superseded_by` — the edge is history, and the audit trail under
the tombstone is the reason the row exists at all — but nothing live depends on
it. Counting it made the successor undeletable for ever, because a tombstone can
never be deleted again to clear the way.

So both deletion blockers (the plan and the cheap header offer) count live
predecessors only, and the tombstone's edge is left exactly as it was.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.deletion import (
    BLOCKED_BY_RELATED_MATTER,
    DELETION_REFUSED,
    REFUSAL_TEXT,
    delete_matter,
    deletion_may_be_offered,
    plan_matter_deletion,
)
from app.matters.models import Matter
from app.workflow.enums import Disposition
from tests import factories
from tests.refusals import refused

pytestmark = pytest.mark.django_db


def _continues(earlier: Matter, later: Matter) -> None:
    Matter.all_objects.filter(pk=earlier.pk).update(
        superseded_by=later,
        is_open=False,
        closed_at=timezone.now(),
        disposition=Disposition.SUPERSEDED,
    )


def _delete(matter: Matter, actor: Any) -> None:
    delete_matter(matter=Matter.objects.get(pk=matter.pk), actor=actor)
    assert Matter.all_objects.get(pk=matter.pk).deleted_at is not None


def _related_blocker(matter: Matter) -> Any:
    plan = plan_matter_deletion(Matter.objects.get(pk=matter.pk))
    found = [b for b in plan.blockers if b.code == BLOCKED_BY_RELATED_MATTER]
    return found[0] if found else None


def test_a_live_predecessor_still_blocks_deleting_its_successor(specialist):
    predecessor = factories.MatterFactory(owner=specialist)
    successor = factories.MatterFactory(owner=specialist)
    _continues(predecessor, successor)

    blocker = _related_blocker(successor)
    assert blocker is not None and blocker.count == 1
    assert deletion_may_be_offered(Matter.objects.get(pk=successor.pk)) is False
    with refused(REFUSAL_TEXT[BLOCKED_BY_RELATED_MATTER]):
        delete_matter(matter=Matter.objects.get(pk=successor.pk), actor=specialist)
    assert Matter.objects.filter(pk=successor.pk).exists()


def test_a_deleted_predecessor_no_longer_blocks_and_keeps_its_edge(specialist):
    predecessor = factories.MatterFactory(owner=specialist)
    successor = factories.MatterFactory(owner=specialist)
    _continues(predecessor, successor)
    _delete(predecessor, specialist)

    assert _related_blocker(successor) is None
    assert deletion_may_be_offered(Matter.objects.get(pk=successor.pk)) is True

    _delete(successor, specialist)

    # The history is not rewritten: the tombstone still names its successor,
    # and both deletions are on the audit trail.
    tombstone = Matter.all_objects.get(pk=predecessor.pk)
    assert tombstone.superseded_by_id == successor.pk
    for gone in (predecessor, successor):
        assert ChangeEvent.objects.filter(
            matter_id=gone.pk, event_type=ChangeEventType.MATTER_DELETED
        ).exists()


def test_several_predecessors_block_only_while_one_of_them_is_live(specialist):
    successor = factories.MatterFactory(owner=specialist)
    predecessors = [factories.MatterFactory(owner=specialist) for _ in range(3)]
    for predecessor in predecessors:
        _continues(predecessor, successor)

    _delete(predecessors[0], specialist)
    _delete(predecessors[1], specialist)
    blocker = _related_blocker(successor)
    assert blocker is not None and blocker.count == 1  # only the live one counts
    assert deletion_may_be_offered(Matter.objects.get(pk=successor.pk)) is False
    with refused(DELETION_REFUSED):
        delete_matter(matter=Matter.objects.get(pk=successor.pk), actor=specialist)

    _delete(predecessors[2], specialist)
    assert _related_blocker(successor) is None
    assert deletion_may_be_offered(Matter.objects.get(pk=successor.pk)) is True
    _delete(successor, specialist)
    assert set(
        Matter.all_objects.filter(pk__in=[p.pk for p in predecessors]).values_list(
            "superseded_by", flat=True
        )
    ) == {successor.pk}
