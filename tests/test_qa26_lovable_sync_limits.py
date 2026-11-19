# ruff: noqa: F811 - the fixtures imported from the contract suite are used as arguments
"""Lovable sync limits and the hand-over notice (quality audit 2026-11-18, package E).

QA26-LS-01  A topic deleted in Lovable stays missing for good; only the rows that
            went missing since the last reading count toward `MAX_MISSING`.
QA26-LS-02  The new-topic breaker holds a night, and one supervised run with
            ``max_new_topics`` creates what it held.
QA26-LS-03  A hand-over made in Lovable, and a Teema the night creates for a
            lawyer, tell the new owner «Uus asi» (owner, 19.11.2026).
QA26-LS-04  An owner's account going off → on → off with no Lovable edit does not
            collide with the first write's idempotency key.

The synthetic world of `tests/lovable_contract/world.py`; limits lowered where
named, to keep the worlds small.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.legacy_import import lovable_sync
from app.legacy_import.lovable_sync import SyncRefused
from app.legacy_import.lovable_sync_models import SyncOutcome, SyncRunStatus, SyncTrigger
from app.matters.models import MatterAssignmentNotice
from tests import factories
from tests.lovable_contract.fixtures import pilot_on  # noqa: F401 - fixture
from tests.lovable_contract.world import (
    AVA,
    UKU,
    build_world,
    decisions,
    lovable_row,
    ref,
    source_id,
)

pytestmark = pytest.mark.django_db

DAY = dt.timedelta(days=1)


# -- LS-01 ----------------------------------------------------------------------------------


def test_deletions_on_separate_nights_never_stop_the_sync(pilot_on, monkeypatch):
    monkeypatch.setattr(lovable_sync, "MAX_MISSING", 2)  # production: 5
    world = build_world(6)
    start = world.runs[0].started_at

    for night, number in ((1, 1), (2, 2), (3, 3), (4, 4)):
        world.lovable.soft_delete(ref(number))
        run = world.run(at=start + night * DAY)
        assert run.status == SyncRunStatus.SUCCEEDED, (night, run.error)
        assert run.counts["missing"] == night  # every deletion is still reported

    world.lovable.edit(ref(5), teema="Parandatud pealkiri Näidis")
    run = world.run(at=start + 5 * DAY)
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert world.matter(ref(5)).title == "Parandatud pealkiri Näidis"
    # The deleted Teemad are kept as Juristid's history.
    assert world.matter(ref(1)).title


def test_many_vanishing_in_one_night_still_stops_it(pilot_on, monkeypatch):
    monkeypatch.setattr(lovable_sync, "MAX_MISSING", 2)
    world = build_world(6)
    for number in (1, 2, 3):
        world.lovable.soft_delete(ref(number))

    run = world.run(at=world.runs[0].started_at + DAY)

    assert run.status == SyncRunStatus.FAILED
    assert run.error.startswith("COHORT_MISSING: 3")


# -- LS-02 ----------------------------------------------------------------------------------


def _three_new_topics(world) -> None:
    for number in (11, 12, 13):
        world.lovable.put(source_id(ref(number)), lovable_row(ref(number)), version=1)


def test_a_supervised_run_creates_what_the_new_topic_breaker_held(pilot_on, monkeypatch):
    monkeypatch.setattr(lovable_sync, "MAX_NEW_TOPICS", 2)  # production: 10
    world = build_world(2)
    start = world.runs[0].started_at
    _three_new_topics(world)

    held = world.run(trigger=SyncTrigger.SCHEDULED, at=start + DAY)
    assert (held.status, held.error, held.counts["new_topics_created"]) == (
        SyncRunStatus.REVIEW_ONLY,
        lovable_sync.NEW_BREAKER,
        0,
    )

    drained = world.run(at=start + 2 * DAY, max_new_topics=3)

    assert drained.status == SyncRunStatus.SUCCEEDED, drained.error
    assert drained.counts["new_topics_created"] == 3
    assert drained.counts["new_topic_limit"] == 3
    for number in (11, 12, 13):
        assert world.matter(ref(number))


def test_the_new_topic_override_is_a_persons_decision_for_one_run(pilot_on):
    world = build_world(2)
    with pytest.raises(SyncRefused) as scheduled:
        lovable_sync.run_sync(trigger=SyncTrigger.SCHEDULED, max_new_topics=50)
    assert scheduled.value.code == "OVERRIDE_NOT_MANUAL"
    with pytest.raises(SyncRefused) as negative:
        lovable_sync.run_sync(trigger=SyncTrigger.MANUAL, max_new_topics=-1)
    assert negative.value.code == "OVERRIDE_NEGATIVE"
    run = world.run(at=world.runs[0].started_at + DAY)
    assert run.counts["new_topic_limit"] == lovable_sync.MAX_NEW_TOPICS


# -- LS-03 ----------------------------------------------------------------------------------


def test_a_handover_in_lovable_and_a_created_teema_tell_the_new_owner(pilot_on):
    from app.notifications.inbox import TYPE_ASSIGNMENT, inbox_for

    world = build_world(2)
    uku = world.person(UKU)
    world.lovable.edit(ref(1), uus_vastutaja=UKU)
    world.lovable.put(source_id(ref(21)), lovable_row(ref(21), vastutaja=UKU), version=1)

    run = world.run()

    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert decisions(run, ref(1))["owner"].outcome == SyncOutcome.APPLIED
    notices = MatterAssignmentNotice.objects.filter(recipient=uku)
    assert sorted(n.matter_id for n in notices) == sorted(
        [world.matter(ref(1)).pk, world.matter(ref(21)).pk]
    )
    assert all(n.assigned_by_id is None for n in notices)
    assert len([i for i in inbox_for(uku) if i.type == TYPE_ASSIGNMENT]) == 2

    # Read once and nothing new: the next night tells nobody again.
    world.run(at=world.runs[0].started_at + DAY)
    assert MatterAssignmentNotice.objects.filter(recipient=uku).count() == 2


def test_other_system_assignments_stay_silent(pilot_on):
    from app.matters.services import assign_matter

    ava = factories.UserFactory()
    matter = factories.MatterFactory(owner=None)
    assign_matter(matter=matter, owner=ava, provenance={"provenance_source": "BACKFILL"})

    assert not MatterAssignmentNotice.objects.filter(recipient=ava).exists()


# -- LS-04 ----------------------------------------------------------------------------------


def _set_active(user, value: bool) -> None:
    user.is_active = value
    user.save(update_fields=["is_active"])


def test_an_owner_account_flipping_off_and_on_never_fails_the_night(pilot_on):
    world = build_world(2)
    ava = world.person(AVA)
    start = world.runs[0].started_at

    expected = {False: AVA, True: ""}
    for night, active in ((1, False), (2, True), (3, False), (4, True), (5, False)):
        _set_active(ava, active)
        run = world.run(at=start + night * DAY)
        assert run.status == SyncRunStatus.SUCCEEDED, (night, run.error)
        assert world.matter(ref(1)).former_owner_name == expected[active], night
