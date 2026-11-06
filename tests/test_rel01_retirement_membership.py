# ruff: noqa: F811 - pytest fixtures imported from the sync suites are used as arguments
"""A cohort retirement must prove the Teema was a member of that cohort (REL-01).

The November 2026 owner brief §17, residuals R2-1 and R2-2 of the post-audit
round (C-L-04, migration legacy_import.0023):

* **R2-1.** `LovableSyncRetirement.save()` checked the tombstone and its event,
  but ``bulk_create`` and a raw INSERT never call it, and 0023's trigger covered
  UPDATE and DELETE only, so a retirement nobody could later remove was
  insertable for a live Teema, another Teema's event or a Teema that was never
  a member.
* **R2-2.** `lovable_sync retire --cohort X` took the cohort from the operator:
  a tombstone of cohort Y, or of no cohort, passed as long as X was short.

The world is the synthetic pilot of four plus the three-Teema operational
cohort of tests/test_lovable_operational_cohort.py. Nothing reaches Lovable.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest
from django.db import DatabaseError, connection, transaction

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.legacy_import import lovable_sync, lovable_sync_retirement
from app.legacy_import.lovable_sync import OPERATIONAL_COHORT, PILOT_COHORT, SyncRefused
from app.legacy_import.lovable_sync_models import LovableSyncRetirement, LovableSyncState
from app.matters import deletion
from app.matters.deletion import permanently_delete_matter, plan_matter_deletion
from app.matters.models import Matter
from tests import factories
from tests.test_lovable_operational_cohort import HOUR, _enroll, active  # noqa: F401
from tests.test_lovable_pilot import pilot_on, world  # noqa: F401
from tests.test_lovable_sync import NIGHT, _matter, _run, cohort  # noqa: F401

pytestmark = pytest.mark.django_db


def _delete(reference: str) -> Matter:
    matter = _matter(reference)
    permanently_delete_matter(
        matter=matter,
        actor=factories.DepartmentHeadFactory(),
        confirmation=plan_matter_deletion(matter).digest,
    )
    return Matter.all_objects.get(pk=matter.pk)


def _delete_without_the_recorder(reference: str) -> Matter:
    """A deletion whose retirement was not recorded (the shape `retire` exists for)."""
    with mock.patch.object(lovable_sync_retirement, "record_for_deletion", return_value=[]):
        return _delete(reference)


def _event(matter: Matter) -> ChangeEvent:
    return ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.MATTER_DELETED)


def _row(matter: Any, event: Any, *, cohort: str = OPERATIONAL_COHORT, **kw: Any) -> Any:
    values: dict[str, Any] = {
        "cohort": cohort,
        "matter_id": matter.pk,
        "deletion_event_id": event.pk,
        "actor_id": getattr(event, "actor_id", None),
        "source_id": None,
        "reference": "2026_512",
        "kind": "OPERATOR",
        "retired_at": NIGHT,
        "reason": "forge",
        **kw,
    }
    return LovableSyncRetirement(**values)


def _raw_insert(row: Any) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO legacy_import_lovablesyncretirement "
            "(id, created_at, updated_at, cohort, matter_id, "
            "deletion_event_id, actor_id, source_id, reference, kind, retired_at, reason) "
            "VALUES (gen_random_uuid(), now(), now(), %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                row.cohort,
                row.matter_id,
                row.deletion_event_id,
                row.actor_id,
                row.source_id,
                row.reference,
                row.kind,
                row.retired_at,
                row.reason,
            ],
        )


def _refused_by_the_database(write: Any) -> None:
    with pytest.raises(DatabaseError), transaction.atomic():
        write()


# ---------------------------------------------------------------------------
# R2-1 reproduction: below the model, the database must refuse a forgery
# ---------------------------------------------------------------------------


def test_r2_1_bulk_create_of_a_live_teemas_retirement_is_refused(active):
    _enroll(active)
    live = _matter("2026_512")
    event = ChangeEvent.objects.filter(matter=live).first()
    assert event is not None
    _refused_by_the_database(lambda: LovableSyncRetirement.objects.bulk_create([_row(live, event)]))
    assert not LovableSyncRetirement.objects.exists()


def test_r2_1_raw_insert_naming_another_teemas_deletion_is_refused(active):
    _enroll(active)
    member = _delete("2026_511")  # retired, with its own event
    other = _delete_without_the_recorder("2026_512")
    _refused_by_the_database(lambda: _raw_insert(_row(other, _event(member))))
    assert LovableSyncRetirement.objects.count() == 1


def test_r2_1_bulk_create_for_a_tombstone_that_was_never_a_member_is_refused(active):
    _enroll(active)
    outsider = factories.MatterFactory()
    permanently_delete_matter(
        matter=outsider,
        actor=factories.DepartmentHeadFactory(),
        confirmation=plan_matter_deletion(outsider).digest,
    )
    tomb = Matter.all_objects.get(pk=outsider.pk)
    row = _row(tomb, _event(tomb))
    _refused_by_the_database(lambda: LovableSyncRetirement.objects.bulk_create([row]))
    _refused_by_the_database(lambda: _raw_insert(row))
    assert not LovableSyncRetirement.objects.exists()


# ---------------------------------------------------------------------------
# R2-2 reproduction: `retire --cohort` must prove membership of that cohort
# ---------------------------------------------------------------------------


def _retire(reference: str, cohort: str, *, apply: bool = True) -> Any:
    return lovable_sync_retirement.retire(
        cohort=cohort, reference=reference, reason="Operaatori kanne", apply=apply
    )


def test_r2_2_a_member_of_another_cohort_is_refused_before_anything_is_written(active):
    _enroll(active)
    assert _run(active).status == "SUCCEEDED"  # the pilot's states are seeded
    # Both cohorts short by one, neither deletion retired: the wrong cohort is short too.
    _delete_without_the_recorder("2026_501")  # a pilot member
    _delete_without_the_recorder("2026_512")  # an operational member
    with pytest.raises(SyncRefused) as refused_:
        _retire("2026_512", PILOT_COHORT)
    assert refused_.value.code == "WRONG_COHORT"
    assert OPERATIONAL_COHORT in str(refused_.value)
    assert not LovableSyncRetirement.objects.exists()
    # The dry run refuses the same way.
    with pytest.raises(SyncRefused) as dry:
        _retire("2026_512", PILOT_COHORT, apply=False)
    assert dry.value.code == "WRONG_COHORT"


def test_r2_2_a_teema_never_enrolled_is_refused_even_with_a_state_on_it(active):
    """A state put on a Teema by hand is not membership: no enrolment names it and
    no cohort provenance does, so its deletion proves nothing about any cohort."""
    _enroll(active)
    _delete_without_the_recorder("2026_512")  # makes the cohort short
    stranger = factories.MatterFactory()
    LovableSyncState.objects.create(
        matter=stranger,
        source_id="00000000-0000-0000-0000-00000000abcd",
        reference=stranger.display_reference[:20],
        cohort=OPERATIONAL_COHORT,
    )
    permanently_delete_matter(
        matter=stranger,
        actor=factories.DepartmentHeadFactory(),
        confirmation=plan_matter_deletion(stranger).digest,
    )
    assert Matter.all_objects.get(pk=stranger.pk).deleted_at is not None
    assert not LovableSyncRetirement.objects.exists()  # the deletion recorded none
    with pytest.raises(SyncRefused) as refused_:
        _retire(stranger.display_reference, OPERATIONAL_COHORT)
    assert refused_.value.code == "NO_MEMBERSHIP_EVIDENCE"
    assert not LovableSyncRetirement.objects.exists()


# ---------------------------------------------------------------------------
# The membership: written at enrolment, kept under the tombstone, append-only
# ---------------------------------------------------------------------------


def _memberships() -> Any:
    from app.legacy_import.lovable_sync_models import LovableSyncMembership

    return LovableSyncMembership.objects


def _enroll_as_before_0024(active: Any) -> None:
    """An enrolment made before memberships were recorded (production's register)."""
    with mock.patch.object(lovable_sync, "remember_membership", return_value=None):
        _enroll(active)


def test_enrolment_records_each_members_cohort_and_lovable_id(active):
    _enroll(active)
    assert _run(active).status == "SUCCEEDED"  # the pilot's states are seeded
    states = {s.matter_id: s for s in LovableSyncState.objects.all()}
    rows = list(_memberships().filter(cohort=OPERATIONAL_COHORT))
    assert len(rows) == 3
    for row in rows:
        state = states[row.matter_id]
        assert row.basis == "ENROLMENT"
        assert (row.cohort, row.source_id) == (state.cohort, state.source_id)
    # The pilot's states, seeded by the nightly run, carry theirs too.
    assert _memberships().filter(cohort=PILOT_COHORT).count() == 4


def test_the_route_deletion_keeps_the_membership_and_retires_from_it(active):
    """End to end: `Kustuta jäädavalt`, the retirement names the membership's
    Lovable id, and the next night succeeds (C-L-04 unchanged)."""
    from tests.test_aud_w1_cohort_retirement import _delete_through_the_route

    _enroll(active)
    assert _run(active).status == "SUCCEEDED"
    victim = _matter("2026_511")
    source_id = LovableSyncState.objects.get(matter=victim).source_id
    assert _delete_through_the_route(victim, factories.DepartmentHeadFactory()) == 302
    membership = _memberships().get(matter=victim)
    assert (membership.cohort, membership.source_id) == (OPERATIONAL_COHORT, source_id)
    retirement = LovableSyncRetirement.objects.get()
    assert (retirement.cohort, retirement.source_id) == (OPERATIONAL_COHORT, source_id)
    assert retirement.deletion_event_id == _event(Matter.all_objects.get(pk=victim.pk)).pk
    run = _run(active, at=NIGHT + HOUR)
    assert run.status == "SUCCEEDED", run.error


def test_a_member_enrolled_before_0024_is_proven_from_provenance_at_deletion(active):
    """The register's 524 states have no membership. Their deletion records one
    from the cohort's provenance, before the purge removes it, and retires."""
    _enroll_as_before_0024(active)
    assert not _memberships().filter(cohort=OPERATIONAL_COHORT).exists()
    state = LovableSyncState.objects.get(matter=_matter("2026_512"))
    tomb = _delete("2026_512")
    membership = _memberships().get(matter=tomb)
    assert (membership.cohort, membership.source_id, membership.basis) == (
        OPERATIONAL_COHORT,
        state.source_id,
        "DELETION",
    )
    assert LovableSyncRetirement.objects.get().source_id == state.source_id
    assert _run(active, at=NIGHT + HOUR).status == "SUCCEEDED"


def test_a_teema_deleted_before_0024_is_refused_and_still_stops_the_night(active):
    """A deletion made by a release without memberships: the purge took the
    state and the provenance, so nothing links cohort, Lovable id and Teema.
    `retire` refuses, naming what is missing; the loss stays drift."""
    _enroll_as_before_0024(active)
    assert _run(active).status == "SUCCEEDED"
    with mock.patch.object(deletion, "_lovable_cohort_members", return_value=[]):
        tomb = _delete("2026_512")
    assert not _memberships().filter(matter=tomb).exists()
    for apply in (False, True):
        with pytest.raises(SyncRefused) as refused_:
            _retire("2026_512", OPERATIONAL_COHORT, apply=apply)
        assert refused_.value.code == "NO_MEMBERSHIP_EVIDENCE"
        message = str(refused_.value)
        assert "No cohort membership record names this Teema" in message
        assert "legacy_import.0024" in message and "nothing was written" in message
    assert not LovableSyncRetirement.objects.exists()
    run = _run(active, at=NIGHT + HOUR)
    assert run.status == "FAILED" and run.error.startswith("COHORT_SIZE")


def test_the_command_refuses_a_wrong_cohort_with_exit_status_2(active):
    from tests.test_aud_w1_cohort_retirement import _retire as command

    _enroll(active)
    assert _run(active).status == "SUCCEEDED"  # the pilot's states are seeded
    _delete_without_the_recorder("2026_501")
    _delete_without_the_recorder("2026_512")
    args = ("--reference", "2026_512", "--reason", "x", "--apply")
    status, out, err = command("--cohort", PILOT_COHORT, *args)
    assert status == 2 and "REFUSED WRONG_COHORT" in err, (out, err)
    assert not LovableSyncRetirement.objects.exists()
    # The right cohort is accepted, and takes the membership's Lovable id.
    status, out, err = command("--cohort", OPERATIONAL_COHORT, *args)
    assert status == 0, err
    row = LovableSyncRetirement.objects.get()
    proof = _memberships().get(matter=row.matter, cohort=OPERATIONAL_COHORT)
    assert row.source_id == proof.source_id


def test_a_changed_lovable_id_is_not_proof_and_records_no_retirement(active):
    """The membership names one id, the state at deletion another: unproven. The
    person's deletion still goes through; the night judges the size as drift."""
    _enroll(active)
    matter = _matter("2026_511")
    LovableSyncState.objects.filter(matter=matter).update(
        source_id="00000000-0000-0000-0000-0000000feed1"
    )
    tomb = _delete("2026_511")
    assert tomb.deleted_at is not None
    assert not LovableSyncRetirement.objects.exists()
    run = _run(active, at=NIGHT + HOUR)
    assert run.status == "FAILED" and run.error.startswith("COHORT_SIZE")


def _raw_membership(matter_id: Any, cohort: str, source_id: Any) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO legacy_import_lovablesyncmembership "
            "(id, created_at, cohort, matter_id, source_id, basis) "
            "VALUES (gen_random_uuid(), now(), %s, %s, %s, 'ENROLMENT')",
            [cohort, matter_id, source_id],
        )


def test_the_database_refuses_an_invented_or_changed_membership(active):
    _enroll_as_before_0024(active)
    live = _matter("2026_510")
    state = LovableSyncState.objects.get(matter=live)
    stranger = factories.MatterFactory()
    tomb = _delete_without_the_recorder("2026_512")
    # A Teema with no state, a tombstone, another id, another cohort: all refused.
    for matter_id, cohort_key, source_id in (
        (stranger.pk, OPERATIONAL_COHORT, state.source_id),
        (tomb.pk, OPERATIONAL_COHORT, state.source_id),
        (live.pk, OPERATIONAL_COHORT, "00000000-0000-0000-0000-0000000feed2"),
        (live.pk, PILOT_COHORT, state.source_id),
    ):
        _refused_by_the_database(
            lambda m=matter_id, c=cohort_key, s=source_id: _raw_membership(m, c, s)
        )
    # The one shape accepted: a live Teema's own state.
    _raw_membership(live.pk, OPERATIONAL_COHORT, state.source_id)
    row = _memberships().get(matter=live)
    for statement in (
        "UPDATE legacy_import_lovablesyncmembership SET cohort = 'x' WHERE id = %s",
        "DELETE FROM legacy_import_lovablesyncmembership WHERE id = %s",
    ):
        with pytest.raises(DatabaseError, match="does not permit"), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(statement, [row.pk])
    assert _memberships().get(pk=row.pk).cohort == OPERATIONAL_COHORT


def test_the_insert_trigger_accepts_the_one_evidenced_shape_and_nothing_near_it(active):
    """Below the model, each link is checked on its own: the trigger refuses a
    row that differs from an evidenced one in any single respect."""
    _enroll(active)
    tomb = _delete_without_the_recorder("2026_512")
    event = _event(tomb)
    membership = _memberships().get(matter=tomb, cohort=OPERATIONAL_COHORT)
    good = {"source_id": membership.source_id}
    stranger = factories.DepartmentHeadFactory()
    for change in (
        {"source_id": None},
        {"source_id": "00000000-0000-0000-0000-0000000feed3"},
        {"cohort": PILOT_COHORT},
        {"actor_id": stranger.pk},
    ):
        _refused_by_the_database(lambda c=change: _raw_insert(_row(tomb, event, **{**good, **c})))
    assert not LovableSyncRetirement.objects.exists()
    LovableSyncRetirement.objects.bulk_create([_row(tomb, event, **good)])
    assert LovableSyncRetirement.objects.get().matter_id == tomb.pk
    assert _run(active, at=NIGHT + HOUR).status == "SUCCEEDED"


def test_a_retirement_anchored_to_the_replacement_is_refused_below_the_model(active):
    from app.matters.deletion import SOURCE_REPLACEMENT, retire_for_source_replacement

    _enroll(active)
    state = LovableSyncState.objects.select_related("matter").get(matter=_matter("2026_510"))
    retire_for_source_replacement(
        matter=state.matter,
        provenance={"source": SOURCE_REPLACEMENT, "cohort": "lovable-register"},
        expected_digest=plan_matter_deletion(state.matter).digest,
    )
    tomb = Matter.all_objects.get(pk=state.matter_id)
    assert not LovableSyncRetirement.objects.exists()
    # Its membership stays under the tombstone, but proves nothing for the replacement.
    assert _memberships().filter(matter=tomb, cohort=OPERATIONAL_COHORT).exists()
    row = _row(tomb, _event(tomb), source_id=state.source_id)
    _refused_by_the_database(lambda: LovableSyncRetirement.objects.bulk_create([row]))
    with pytest.raises(SyncRefused) as refused_:
        _retire("2026_510", OPERATIONAL_COHORT)
    assert refused_.value.code == "REPLACEMENT"
