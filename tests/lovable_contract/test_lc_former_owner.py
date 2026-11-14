# ruff: noqa: F811 - the fixtures imported from fixtures.py are used as arguments
"""The former owner's name (owner decision 14.11.2026).

Lovable names an owner who has no assignable Juristid account — a lawyer who
has left the Chamber. No user is created; the name is kept on the Teema as
`Matter.former_owner_name`, open or closed, shown muted and searchable, and
cleared once the owner resolves or Lovable names no one. Synthetic names only.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.accounts.models import User
from app.legacy_import import lovable_sync
from app.legacy_import.lovable_sync_models import SyncMode, SyncOutcome, SyncRunStatus
from tests import factories
from tests.lovable_contract import oracle
from tests.lovable_contract.fixtures import pilot_on  # noqa: F401 - fixture
from tests.lovable_contract.world import UKU, build_world, decisions, ref

pytestmark = pytest.mark.django_db

FORMER = "Endine Näidisjurist"


def _former_world():
    """2099_1 open, 2099_2 closed; Lovable names a person Juristid does not know on both."""
    world = build_world(3, closed=(2,))
    for number in (1, 2):
        world.lovable.edit(ref(number), uus_vastutaja=FORMER)
    return world


def test_an_unresolved_owner_name_is_kept_on_open_and_closed_teemad(db, pilot_on):
    """Feature: former owner name
    Regression: an owner Lovable names who has no Juristid account was only a
    held owner decision, and the Teema read as ownerless. One APPLY run now
    keeps the name on an open and on a closed Teema, through the service, with
    the run's provenance; no user is created; the next run changes nothing."""
    world = _former_world()
    users = User.objects.count()
    run = world.run()
    assert (run.status, run.verified) == (SyncRunStatus.SUCCEEDED, True), run.error
    for number in (1, 2):
        matter = world.matter(ref(number))
        assert matter.former_owner_name == FORMER, number
        decision = decisions(run, ref(number))[lovable_sync.FORMER_OWNER]
        assert decision.outcome == SyncOutcome.APPLIED, number
        event = world.events_of(run).get(
            matter=matter, payload__fields=["former_owner_name"], actor__isnull=True
        )
        assert event.payload["current"] == FORMER
        assert event.payload["sync_run"] == str(run.pk)
        assert event.payload["provenance_source"] == lovable_sync.PROVENANCE_SOURCE
    assert not world.matter(ref(2)).is_open
    assert User.objects.count() == users
    # The owner decision is exactly as before: held, never invented.
    assert decisions(run, ref(1))["owner"].outcome == SyncOutcome.REVIEW_REQUIRED
    assert oracle.differences(world.lovable, today=world.today) == []

    again = world.run()
    assert again.status == SyncRunStatus.SUCCEEDED, again.error
    assert not again.decisions.filter(outcome=SyncOutcome.APPLIED).exists()
    assert world.matter(ref(1)).former_owner_name == FORMER


def test_an_owner_who_resolves_leaves_the_former_name_empty(db, pilot_on):
    """Feature: former owner name
    Regression: a resolvable owner must never be recorded as «former»: the
    owner is assigned as before and `former_owner_name` stays empty."""
    world = build_world(2)
    world.lovable.edit(ref(1), uus_vastutaja=UKU)
    run = world.run()
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    matter = world.matter(ref(1))
    assert matter.owner == world.person(UKU)
    assert matter.former_owner_name == ""
    written = decisions(run, ref(1)).get(lovable_sync.FORMER_OWNER)
    assert written is None or written.outcome != SyncOutcome.APPLIED
    assert oracle.differences(world.lovable, today=world.today) == []


def test_a_name_that_becomes_a_known_user_clears_the_former_name(db, pilot_on):
    """Feature: former owner name
    Regression: when Lovable moves the owner from an unknown name to a Juristid
    user, the former name is cleared (open and closed alike) and the open
    Teema gets its owner."""
    world = _former_world()
    world.run()
    assert world.matter(ref(1)).former_owner_name == FORMER
    for number in (1, 2):
        world.lovable.edit(ref(number), uus_vastutaja=UKU)
    run = world.run()
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert world.matter(ref(1)).former_owner_name == ""
    assert world.matter(ref(2)).former_owner_name == ""
    assert world.matter(ref(1)).owner == world.person(UKU)
    assert oracle.differences(world.lovable, today=world.today) == []


def test_compare_writes_no_former_name(db, pilot_on):
    """Feature: former owner name
    Regression: COMPARE reports the name it would keep and writes nothing."""
    world = _former_world()
    run = world.run(mode=SyncMode.COMPARE)
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert world.matter(ref(1)).former_owner_name == ""
    assert world.matter(ref(2)).former_owner_name == ""
    assert not world.events_of(run).filter(payload__fields=["former_owner_name"]).exists()
    assert decisions(run, ref(1))[lovable_sync.FORMER_OWNER].outcome == SyncOutcome.ELIGIBLE


def test_the_former_name_is_shown_muted_and_found_by_search(client, specialist, reader):
    """Feature: former owner name
    Regression: a Teema with no owner but a former owner's name must not read
    as ownerless: the header and the register show «Endine vastutaja» / «endine:»
    muted — never as the current owner — and search finds the Teema by it."""
    from app.search.services import search_matters

    matter = factories.MatterFactory(owner=None, former_owner_name=FORMER)
    client.force_login(specialist)
    page = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    body = page.content.decode()
    assert page.status_code == 200
    assert f"Endine vastutaja: {FORMER}" in body
    assert 'class="metaline__value muted" data-former-owner' in body

    # A reader is offered no «Määra» menu, so the cell shows what is known.
    client.force_login(reader)
    register = client.get(reverse("matters:matter_list") + "?olek=koik")
    assert register.status_code == 200
    assert f"endine: {FORMER}" in register.content.decode()

    found = search_matters(query="Näidisjurist", user=specialist)
    assert matter.pk in {result.matter.pk for result in found}
