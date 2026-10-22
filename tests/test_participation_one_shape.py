"""Participation has one SQL shape, and it selects exactly what the join selected (QRY-05).

`restricted_participation_q` reached a Matter's collaborators through a
``LEFT OUTER JOIN`` that multiplied a Matter by its collaborators, and `apply`
collapsed that with ``SELECT DISTINCT`` on every reader's and administrator's
query. It is the uncorrelated subquery search already used; `apply` keeps
``DISTINCT`` only where a query still fans out.

Authorization-critical, so the claim is checked row for row: for every persona,
every scoped model returns the same primary keys as the old join + DISTINCT
spelling — no row gained, none lost, none repeated — over a world with every
participation shape (normal, owned, collaborator, several collaborators,
non-participant, a restricted child under a normal Matter).

Since docs/adr/0181 no request reaches a narrowed scope: `scope_for_user` gives
a business-read role everything and anybody else nothing. The participation
builder is still what a hand-built scope gets, so it is checked here through
`_narrowed(user)` — a scope that knows the person and does not read RESTRICTED
by role — and the request scopes are checked for being all-or-nothing.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db.models import Q

from app.core.authorization import (
    Scope,
    apply,
    child_is_normal_q,
    child_visibility_q,
    matter_visibility_q,
    scope_for_user,
)
from app.core.enums import Visibility
from app.matters.models import Entry, Matter
from app.workflow.models import NextAction, next_action_visibility_q
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db

#: Personas whose participation the builder is asked about, through a hand-built scope.
NARROWED = ["admin_participant", "reader_participant", "reader_outsider"]

PERSONAS = [*NARROWED, "specialist", "department_head"]


@pytest.fixture
def world(specialist, other_specialist, department_head, reader, administrator):
    """Participation used to be consulted for READER and ADMINISTRATOR; since
    docs/adr/0181 only a hand-built scope consults it (`_narrowed`)."""
    collaborating_reader = factories.ReaderFactory()
    third = factories.UserFactory()
    fourth = factories.UserFactory()
    normal = factories.MatterFactory(owner=other_specialist, visibility=Visibility.NORMAL)
    owned = factories.MatterFactory(owner=administrator, visibility=Visibility.RESTRICTED)
    shared = factories.MatterFactory(owner=other_specialist, visibility=Visibility.RESTRICTED)
    shared.collaborators.add(administrator)
    crowded = factories.MatterFactory(owner=other_specialist, visibility=Visibility.RESTRICTED)
    crowded.collaborators.add(administrator, collaborating_reader, third, fourth)
    closed_off = factories.MatterFactory(owner=other_specialist, visibility=Visibility.RESTRICTED)
    closed_off.collaborators.add(third)
    for matter in (normal, owned, shared, crowded, closed_off):
        factories.EntryFactory(matter=matter, author=other_specialist)
        factories.EntryFactory(
            matter=matter, author=other_specialist, visibility_override=Visibility.RESTRICTED
        )
        set_next_action(matter=matter, text="Samm", actor=other_specialist)
    return {
        "admin_participant": administrator,
        "reader_participant": collaborating_reader,
        "reader_outsider": reader,
        "specialist": specialist,
        "department_head": department_head,
    }


def _narrowed(user: Any) -> Scope:
    """A scope that knows the person and does not read RESTRICTED by role.

    `scope_for_user` builds none since docs/adr/0181; the builders still serve one.
    """
    return Scope(
        user=user,
        is_authenticated=True,
        sees_restricted_by_role=False,
        break_glass_grant_id=None,
    )


def _matters(scope: Scope):
    return apply(Matter.objects.all(), matter_visibility_q(scope))


def _entries(scope: Scope):
    return apply(Entry.objects.all(), child_visibility_q(scope)).live()


def _actions(scope: Scope):
    return apply(NextAction.objects.all(), next_action_visibility_q(scope))


def _old_matter_q(user: Any) -> Q:
    """The join spelling, as it stood: normal, or owned, or collaborated on."""
    return Q(visibility=Visibility.NORMAL) | Q(owner=user) | Q(collaborators=user)


def _old_child_q(user: Any) -> Q:
    return child_is_normal_q() | Q(matter__owner=user) | Q(matter__collaborators=user)


def _ids(queryset) -> list:
    return sorted(queryset.values_list("pk", flat=True))


@pytest.mark.parametrize("persona", NARROWED)
def test_every_scoped_model_selects_what_the_join_selected(world, persona):
    """The participation builder, through a hand-built narrowed scope."""
    user = world[persona]
    scope = _narrowed(user)

    new_matters = _ids(_matters(scope))
    new_entries = _ids(_entries(scope))
    new_actions = _ids(_actions(scope))
    old_matters = _ids(Matter.objects.filter(_old_matter_q(user)).distinct())
    old_entries = _ids(Entry.objects.filter(_old_child_q(user)).distinct())
    old_actions = _ids(NextAction.objects.filter(_old_child_q(user)).distinct())

    assert new_matters == old_matters
    assert new_entries == old_entries
    assert new_actions == old_actions
    # And nothing repeats now that nothing de-duplicates.
    for ids in (new_matters, new_entries, new_actions):
        assert len(ids) == len(set(ids))


@pytest.mark.parametrize("persona", PERSONAS)
def test_every_request_scope_is_all_or_nothing(world, persona):
    """docs/adr/0181: a business-read role reads every row, an administrator none."""
    user = world[persona]
    scope = scope_for_user(user)

    new_matters = _ids(Matter.objects.visible_to(user))
    new_entries = _ids(Entry.objects.visible_to(user))
    new_actions = _ids(NextAction.objects.visible_to(user))
    if persona == "admin_participant":
        assert not scope.is_authenticated
        assert new_matters == new_entries == new_actions == []
    else:
        assert scope.sees_all_restricted
        assert new_matters == _ids(Matter.objects.all())
        assert new_entries == _ids(Entry.objects.all())
        assert new_actions == _ids(NextAction.objects.all())
    for ids in (new_matters, new_entries, new_actions):
        assert len(ids) == len(set(ids))


def test_a_narrowed_query_no_longer_joins_or_deduplicates(world):
    """Was asked of a READER's request; since docs/adr/0181 only a hand-built scope narrows."""
    sql = str(_matters(_narrowed(world["reader_outsider"])).query)

    assert "matters_matter_collaborators" in sql  # the rule is still there …
    assert "LEFT OUTER JOIN" not in sql  # … as a subquery, not a join
    assert "SELECT DISTINCT" not in sql


def test_the_participant_sees_the_crowded_matter_once(world):
    """Four collaborators used to be four rows before DISTINCT (hand-built scopes)."""
    admin = _ids(_matters(_narrowed(world["admin_participant"])))
    reader = _ids(_matters(_narrowed(world["reader_participant"])))
    outsider = _ids(_matters(_narrowed(world["reader_outsider"])))

    assert len(admin) == len(set(admin)) == 4  # normal, owned, shared, crowded
    assert len(reader) == len(set(reader)) == 2  # normal, crowded
    assert len(outsider) == 1  # normal only


def test_a_query_that_still_fans_out_keeps_its_distinct(world):
    """`apply` de-duplicates exactly where a join can still repeat a row."""
    scope = _narrowed(world["reader_outsider"])
    fanned = apply(Matter.objects.filter(entries__isnull=False), matter_visibility_q(scope))

    assert fanned.query.distinct
    assert _ids(fanned) == sorted(set(_ids(fanned)))


def test_a_scope_that_knows_nobody_still_sees_nothing_restricted(world):
    from django.contrib.auth.models import AnonymousUser

    assert not Matter.objects.visible_to(AnonymousUser()).exists()
