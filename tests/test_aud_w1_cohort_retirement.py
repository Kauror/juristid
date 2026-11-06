# ruff: noqa: F811 - pytest fixtures imported from the sync suites are used as arguments
"""A permanently deleted cohort Teema leaves its cohort; drift still stops the night.

Audit 2026-11 C-L-04 (P2): after one cohort Teema was permanently deleted
(docs/adr/0165, `matters:matter_delete`), every later Lovable synchronisation
run failed with COHORT_SIZE, and no operator path re-sized the cohort. The
correction records an explicit, attributable retirement
(`LovableSyncRetirement`) in the deletion's own transaction, tied to its
MATTER_DELETED audit event; a cohort's required size is its enrolled size less
its authorised retirements. A state that vanishes without one is still drift
and still stops the night (owner safeguard, 2026-11-06).

The world is the synthetic pilot of four plus the three-Teema operational
cohort of tests/test_lovable_operational_cohort.py. Nothing reaches Lovable.
"""

from __future__ import annotations

import io
import json
import re
from typing import Any
from unittest import mock

import pytest
from django.core.management import CommandError, call_command
from django.db import DatabaseError, connection, transaction
from django.test import Client
from django.urls import reverse

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.legacy_import import lovable_sync
from app.legacy_import.lovable_sync import OPERATIONAL_COHORT, PILOT_COHORT, SyncRefused
from app.legacy_import.lovable_sync_models import (
    LovableSyncCohort,
    LovableSyncState,
    SyncMode,
    SyncOutcome,
    SyncRunStatus,
    SyncTrigger,
)
from app.legacy_import.models import EvidencePilotRecord
from app.matters.deletion import permanently_delete_matter, plan_matter_deletion
from app.matters.models import Matter
from tests import factories
from tests.refusals import refused
from tests.test_lovable_operational_cohort import HOUR, _enroll, active  # noqa: F401
from tests.test_lovable_pilot import pilot_on, world  # noqa: F401
from tests.test_lovable_sync import NIGHT, _decisions, _matter, _run, cohort  # noqa: F401

pytestmark = pytest.mark.django_db


def _retirements() -> Any:
    from app.legacy_import.lovable_sync_models import LovableSyncRetirement

    return LovableSyncRetirement.objects


def _delete_through_the_route(matter: Matter, head: Any) -> int:
    """`Kustuta jäädavalt` as a department head: the confirmation, then the POST."""
    client = Client()
    client.force_login(head)
    url = reverse("matters:matter_delete", kwargs={"pk": matter.pk})
    confirm = client.get(url)
    assert confirm.status_code == 200
    digest = re.search(r'value="([0-9a-f]{64})"', confirm.content.decode())
    assert digest is not None
    response = client.post(url, {"confirm_digest": digest.group(1)})
    return response.status_code


def _delete(reference: str, head: Any = None) -> Matter:
    """The product's deletion service, as an authorised person."""
    matter = _matter(reference)
    head = head or factories.DepartmentHeadFactory()
    permanently_delete_matter(
        matter=matter, actor=head, confirmation=plan_matter_deletion(matter).digest
    )
    return Matter.all_objects.get(pk=matter.pk)


def _deletion_event(matter: Matter) -> ChangeEvent:
    return ChangeEvent.objects.get(matter=matter, event_type=ChangeEventType.MATTER_DELETED)


def _retire(*args: str) -> tuple[int, str, str]:
    """`lovable_sync retire …`: (exit status, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    try:
        call_command("lovable_sync", "retire", *args, stdout=out, stderr=err)
    except SystemExit as stop:
        return int(stop.code or 0), out.getvalue(), err.getvalue()
    except CommandError as error:
        return 1, out.getvalue(), str(error)
    return 0, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# 1. The real deletion route on a cohort member, then the next run
# ---------------------------------------------------------------------------


def test_cl04_the_night_after_a_route_deletion_succeeds(active):
    """The audit's reproduction, unchanged in shape: on d4accd22 both later runs
    fail with COHORT_SIZE (agent H, run4-lovable.log)."""
    _enroll(active)
    assert _run(active).status == SyncRunStatus.SUCCEEDED
    head = factories.DepartmentHeadFactory(display_name="Juht Lovable W1")
    assert _delete_through_the_route(_matter("2026_511"), head) == 302
    runs = [_run(active, at=NIGHT + HOUR * k) for k in (1, 2)]
    assert [(r.status, r.error) for r in runs] == [(SyncRunStatus.SUCCEEDED, "")] * 2


def test_a_deleted_cohort_teema_is_retired_and_the_next_night_succeeds(active):
    _enroll(active)
    assert _run(active).status == SyncRunStatus.SUCCEEDED
    victim = _matter("2026_511")
    state = LovableSyncState.objects.get(matter=victim)
    head = factories.DepartmentHeadFactory(display_name="Juht Kohort")

    assert _delete_through_the_route(victim, head) == 302

    tomb = Matter.all_objects.get(pk=victim.pk)
    assert tomb.deleted_at is not None
    assert not LovableSyncState.objects.filter(matter=victim).exists()
    event = _deletion_event(tomb)
    assert event.actor_id == head.pk
    retirement = _retirements().get()
    assert (retirement.cohort, retirement.matter_id, retirement.deletion_event_id) == (
        OPERATIONAL_COHORT,
        victim.pk,
        event.pk,
    )
    assert retirement.actor_id == head.pk
    assert str(retirement.source_id) == str(state.source_id)
    assert retirement.reference == "2026_511"
    assert retirement.kind == "MATTER_DELETED"
    assert retirement.retired_at == event.occurred_at

    # The required size is the enrolled size less the authorised retirement.
    row = LovableSyncCohort.objects.get(key=OPERATIONAL_COHORT)
    assert row.expected == 3  # the enrolment's own record is never rewritten
    assert lovable_sync.expected_cohorts() == {PILOT_COHORT: 4, OPERATIONAL_COHORT: 2}

    later = [_run(active, at=NIGHT + HOUR * k) for k in (1, 2)]
    for run in later:
        assert run.status == SyncRunStatus.SUCCEEDED, run.error
        assert run.mode == SyncMode.COMPARE
        assert run.counts["checked"] == 6
        assert run.counts["retired"] == 1
        assert run.counts["by_cohort"][OPERATIONAL_COHORT]["expected"] == 2
        assert run.counts["by_cohort"][OPERATIONAL_COHORT]["retired"] == 1
    assert any("jäädavalt kustutatud" in line for line in lovable_sync.summary_lines(later[-1]))
    # COMPARE stays COMPARE: the retirement opened no write.
    assert lovable_sync.control().mode == SyncMode.COMPARE
    assert not later[-1].decisions.filter(outcome=SyncOutcome.APPLIED).exists()


def test_a_pilot_member_deleted_through_the_service_is_retired_too(cohort):
    lovable_sync.set_mode(SyncMode.COMPARE, reason="test")
    assert _run(cohort).status == SyncRunStatus.SUCCEEDED
    tomb = _delete("2026_503")
    assert _retirements().get().cohort == PILOT_COHORT
    assert _retirements().get().deletion_event_id == _deletion_event(tomb).pk
    assert lovable_sync.expected_cohorts() == {PILOT_COHORT: 3}
    run = _run(cohort, at=NIGHT + HOUR)
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert run.counts["checked"] == 3


def test_kontrolli_muudatusi_after_a_retirement_compares_the_rest(active):
    _enroll(active)
    assert _run(active).status == SyncRunStatus.SUCCEEDED
    _delete("2026_512")
    check = lovable_sync.run_sync(trigger=SyncTrigger.CHECK, now=lambda: NIGHT + HOUR)
    assert check.status == SyncRunStatus.SUCCEEDED, check.error
    assert check.counts["checked"] == 6 and check.counts["retired"] == 1


def test_report_and_status_show_each_retirement_without_naming_the_teema(active):
    _enroll(active)
    _delete("2026_510")
    out = io.StringIO()
    call_command("lovable_sync", "report", "--json", stdout=out)
    report = json.loads(out.getvalue())
    ledger = report["retirements"]
    assert ledger["cohorts"][OPERATIONAL_COHORT] == {"enrolled": 3, "retired": 1, "expected": 2}
    assert ledger["cohorts"][PILOT_COHORT] == {"enrolled": 4, "retired": 0, "expected": 4}
    assert len(ledger["retired"]) == 1
    entry = ledger["retired"][0]
    assert (entry["cohort"], entry["kind"], entry["attributed"]) == (
        OPERATIONAL_COHORT,
        "MATTER_DELETED",
        True,
    )
    assert entry["retired_at"]
    assert report["status"]["cohorts"][OPERATIONAL_COHORT]["expected"] == 2
    assert "2026_510" not in out.getvalue()
    # The observability status no longer warns about the size.
    from django.contrib.auth.models import AnonymousUser

    from app.legacy_import import lovable_observability

    status = lovable_observability.status(AnonymousUser())
    assert not any("mitte 3" in warning for warning in status.warnings)


def test_production_status_reads_the_night_after_a_retirement(active):
    from app.core.production_status import _lovable_sync

    _enroll(active)
    _delete("2026_511")
    run = _run(active, at=NIGHT + HOUR, trigger=SyncTrigger.MANUAL)
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    health = lovable_sync.sync_health(now=NIGHT + 2 * HOUR)
    assert health.last_run == run
    assert "failed" not in health.detail
    row = _lovable_sync()
    assert row.label == "Lovable sync" and "COHORT_SIZE" not in row.detail


# ---------------------------------------------------------------------------
# 2–3. Drift, a missing topic and a broken read still stop the night
# ---------------------------------------------------------------------------


def _drift(reference: str) -> None:
    """A member lost without any deletion: its provenance and its state gone.
    (A state alone is re-seeded from provenance by the next night's
    `ensure_states`, as before; `Kontrolli muudatusi` seeds nothing.)"""
    matter = _matter(reference)
    EvidencePilotRecord.objects.filter(matter=matter, record_kind="MATTER").delete()
    LovableSyncState.objects.filter(matter=matter).delete()


def test_a_state_removed_without_a_retirement_still_fails_cohort_size(active):
    _enroll(active)
    assert _run(active).status == SyncRunStatus.SUCCEEDED
    _drift("2026_512")
    run = _run(active, at=NIGHT + HOUR)
    assert run.status == SyncRunStatus.FAILED
    assert run.error.startswith("COHORT_SIZE")
    assert "lovable-operational holds 2 Teemad, not 3" in run.error
    assert not _retirements().exists()


def test_a_check_finds_a_lost_state_without_a_retirement(active):
    _enroll(active)
    assert _run(active).status == SyncRunStatus.SUCCEEDED
    LovableSyncState.objects.filter(matter=_matter("2026_512")).delete()
    check = lovable_sync.run_sync(trigger=SyncTrigger.CHECK, now=lambda: NIGHT + HOUR)
    assert check.status == SyncRunStatus.FAILED
    assert "lovable-operational holds 2 Teemad, not 3" in check.error


def test_drift_beside_an_authorised_retirement_still_fails_cohort_size(active):
    _enroll(active)
    _delete("2026_511")
    _drift("2026_512")
    run = _run(active, at=NIGHT + HOUR)
    assert run.status == SyncRunStatus.FAILED
    assert "lovable-operational holds 1 Teemad, not 2" in run.error


def test_a_retired_members_state_that_comes_back_fails_cohort_size(active):
    _enroll(active)
    state = LovableSyncState.objects.get(matter=_matter("2026_511"))
    values = {
        f.attname: getattr(state, f.attname)
        for f in LovableSyncState._meta.concrete_fields
        if f.attname not in ("id", "matter_id")
    }
    _delete("2026_511")
    # A state re-created on the tombstone (drift): one more than the cohort may hold.
    LovableSyncState.objects.create(matter_id=_matter("2026_511").pk, **values)
    run = _run(active, at=NIGHT + HOUR)
    assert run.status == SyncRunStatus.FAILED
    assert "lovable-operational holds 3 Teemad, not 2" in run.error


def test_a_vanished_topic_after_a_retirement_is_still_source_missing(active, monkeypatch):
    _enroll(active)
    _delete("2026_511")
    # The source loses another member's topic: that Teema only, as before.
    gone = next(t for t in active["active_topics"] if t["reference"] == "2026_510")
    del active["source"].rows[gone["source"]["id"]]
    run = _run(active, at=NIGHT + HOUR)
    assert _decisions(run, "2026_510")["*"].outcome == SyncOutcome.SOURCE_MISSING
    assert run.counts["missing"] == 1
    # And a truncated read beyond the limit stops the whole night, as before.
    monkeypatch.setattr(lovable_sync, "MAX_MISSING", 0)
    stopped = _run(active, at=NIGHT + 2 * HOUR)
    assert stopped.status == SyncRunStatus.FAILED
    assert stopped.error.startswith("COHORT_MISSING")


def test_a_broken_read_after_a_retirement_still_fails(active):
    from app.legacy_import.lovable_source import SourceError

    _enroll(active)
    _delete("2026_511")
    active["source"].fail = SourceError("HTTP_500", "synthetic")
    run = _run(active, at=NIGHT + HOUR)
    assert run.status == SyncRunStatus.FAILED
    assert run.error.startswith("HTTP_500")


# ---------------------------------------------------------------------------
# 4. Idempotent, attributed; a double retirement and a non-member refused
# ---------------------------------------------------------------------------


def test_the_retirement_is_recorded_once_and_cannot_be_changed(active):
    from app.core.errors import ImmutableRecordError
    from app.legacy_import import lovable_sync_retirement

    _enroll(active)
    tomb = _delete("2026_511")
    # The same deletion again changes nothing (delete_matter reads it as done).
    head = factories.DepartmentHeadFactory()
    permanently_delete_matter(matter=tomb, actor=head, confirmation="0" * 64)
    assert _retirements().count() == 1
    # The recorder itself is idempotent for the same deletion event.
    retirement = _retirements().get()
    again = lovable_sync_retirement.record(
        cohort=retirement.cohort,
        matter=tomb,
        deletion_event=_deletion_event(tomb),
        actor=retirement.actor,
        source_id=retirement.source_id,
        reference=retirement.reference,
        kind=retirement.kind,
        reason=retirement.reason,
    )
    assert again.pk == retirement.pk and _retirements().count() == 1
    retirement.reason = "muudetud"
    with pytest.raises(ImmutableRecordError, match="ei muudeta"):
        retirement.save()


def test_the_operator_command_repairs_a_deletion_made_before_the_fix(active):
    from app.legacy_import import lovable_sync_retirement

    _enroll(active)
    assert _run(active).status == SyncRunStatus.SUCCEEDED
    # A deletion made by a release without the recorder: the night stops.
    with mock.patch.object(lovable_sync_retirement, "record_for_deletion", lambda **_kw: []):
        _delete("2026_512")
    stopped = _run(active, at=NIGHT + HOUR)
    assert stopped.status == SyncRunStatus.FAILED and stopped.error.startswith("COHORT_SIZE")

    # Dry run by default: says what it would record, records nothing.
    args = ["--cohort", OPERATIONAL_COHORT, "--reference", "2026_512", "--reason", "Kustutatud"]
    status, out, err = _retire(*args)
    assert status == 0, err
    assert "dry run" in out and not _retirements().exists()

    status, out, err = _retire(*args, "--apply")
    assert status == 0, err
    retirement = _retirements().get()
    tomb = _matter("2026_512")
    assert retirement.kind == "OPERATOR"
    assert retirement.deletion_event_id == _deletion_event(tomb).pk
    assert retirement.actor_id == _deletion_event(tomb).actor_id
    assert retirement.source_id is None and retirement.reason == "Kustutatud"
    assert "retired" in out

    run = _run(active, at=NIGHT + 2 * HOUR)
    assert run.status == SyncRunStatus.SUCCEEDED, run.error

    # A second retirement of the same Teema is refused.
    status, _out, err = _retire(*args, "--apply")
    assert status == 2 and "REFUSED ALREADY_RETIRED" in err
    assert _retirements().count() == 1


def test_an_automatically_retired_teema_cannot_be_retired_again(active):
    _enroll(active)
    _delete("2026_511")
    status, _out, err = _retire(
        "--cohort", OPERATIONAL_COHORT, "--reference", "2026_511", "--reason", "x", "--apply"
    )
    assert status == 2 and "REFUSED ALREADY_RETIRED" in err
    assert _retirements().count() == 1


def test_a_live_member_is_never_retired(active):
    _enroll(active)
    with pytest.raises(SyncRefused, match="only through its permanent deletion") as refused:
        _plan("2026_511", apply=True)
    assert refused.value.code == "NOT_DELETED"
    assert not _retirements().exists()


def test_a_deleted_teema_that_held_no_state_is_not_a_member(active):
    _enroll(active)
    outsider = factories.MatterFactory()
    tomb = _delete(outsider.display_reference)
    assert not LovableSyncState.objects.filter(matter=tomb).exists()
    with pytest.raises(SyncRefused, match="held no synchronisation state") as refused:
        _plan(outsider.display_reference, apply=True)
    assert refused.value.code == "NOT_A_MEMBER"
    assert not _retirements().exists()


def test_a_retirement_is_refused_when_the_cohort_is_whole(active, monkeypatch):
    from app.legacy_import import lovable_sync_retirement

    _enroll(active)
    with mock.patch.object(lovable_sync_retirement, "record_for_deletion", lambda **_kw: []):
        _delete("2026_512")
    # The cohort reads as whole (as if a state had been put back by hand).
    held = LovableSyncState.objects.filter(cohort=OPERATIONAL_COHORT).count()
    monkeypatch.setattr(
        lovable_sync_retirement, "_held", lambda key: held + 1 if key == OPERATIONAL_COHORT else 0
    )
    with pytest.raises(SyncRefused, match="already holds") as refused:
        _plan("2026_512", apply=True)
    assert refused.value.code == "COHORT_WHOLE"


def test_the_operator_command_refuses_what_it_cannot_attribute(active):
    _enroll(active)
    _delete("2026_511")
    cases = {
        ("--cohort", OPERATIONAL_COHORT, "--reference", "2026_511"): "needs --cohort",
        ("--cohort", "tundmatu", "--reference", "2026_511", "--reason", "x"): "UNKNOWN_COHORT",
        ("--cohort", OPERATIONAL_COHORT, "--reference", "2026_999", "--reason", "x"): "NOT_FOUND",
    }
    for args, code in cases.items():
        status, _out, err = _retire(*args)
        assert status != 0 and code in err, (args, err)


def test_a_retirement_after_final_cutover_is_refused(active):
    _enroll(active)
    lovable_sync.cut_over(reason="test")
    with pytest.raises(SyncRefused, match="Final cutover") as refused:
        _plan("2026_511")
    assert refused.value.code == "CUT_OVER"


def _plan(reference: str, *, apply: bool = False, cohort: str = OPERATIONAL_COHORT) -> Any:
    from app.legacy_import import lovable_sync_retirement

    return lovable_sync_retirement.retire(
        cohort=cohort, reference=reference, reason="Operaatori kanne", apply=apply
    )


# ---------------------------------------------------------------------------
# 5. Never below zero; re-enrolment and the replacement still work
# ---------------------------------------------------------------------------


def test_every_member_retired_is_an_empty_cohort_never_a_negative_one(active):
    _enroll(active)
    for reference in ("2026_510", "2026_511", "2026_512"):
        _delete(reference)
    assert lovable_sync.expected_cohorts()[OPERATIONAL_COHORT] == 0
    run = _run(active, at=NIGHT + HOUR)
    assert run.status == SyncRunStatus.SUCCEEDED, run.error
    assert run.counts["by_cohort"][OPERATIONAL_COHORT]["checked"] == 0
    # More retirements than the enrolment (only drift could write one) never go below 0.
    # The extra row is the one shape the model still accepts: a tombstone beside
    # its own MATTER_DELETED event, here a Teema that was never a member.
    outsider = factories.MatterFactory()
    permanently_delete_matter(
        matter=outsider,
        actor=factories.DepartmentHeadFactory(),
        confirmation=plan_matter_deletion(outsider).digest,
    )
    extra = Matter.all_objects.get(pk=outsider.pk)
    event = _deletion_event(extra)
    _retirements().create(
        cohort=OPERATIONAL_COHORT,
        matter=extra,
        deletion_event=event,
        reference="",
        kind="OPERATOR",
        retired_at=NIGHT,
        reason="drift",
    )
    assert lovable_sync.expected_cohorts()[OPERATIONAL_COHORT] == 0
    ledger = {c["key"]: c for c in lovable_sync.cohort_ledger()}
    assert ledger[OPERATIONAL_COHORT] == {
        "key": OPERATIONAL_COHORT,
        "enrolled": 3,
        "retired": 4,
        "expected": 0,
    }


def test_the_same_enrolment_again_is_still_a_no_op_after_a_retirement(active):
    _enroll(active)
    _delete("2026_511")
    again = _enroll(active)
    assert again.already and again.expected == 3 and again.seeded == 2
    # A different size is still refused: the retirement, not a re-enrolment, re-sizes.
    with pytest.raises(SyncRefused, match="already enrolled with another package") as refused:
        _enroll(active, expect=2)
    assert refused.value.code == "COHORT_ENROLLED"
    assert _run(active, at=NIGHT + HOUR).status == SyncRunStatus.SUCCEEDED


def test_the_replacement_retires_whole_cohorts_without_member_retirements(active):
    """`retire_for_source_replacement` (docs/adr/0203) records no member retirement:
    the replacement's own enrolment retires the cohorts it replaces."""
    from app.matters.deletion import SOURCE_REPLACEMENT, retire_for_source_replacement

    _enroll(active)
    _delete("2026_510")  # a person's deletion before the replacement: one retirement
    for state in list(LovableSyncState.objects.select_related("matter")):
        retire_for_source_replacement(
            matter=state.matter,
            provenance={"source": SOURCE_REPLACEMENT, "cohort": "lovable-register"},
            expected_digest=plan_matter_deletion(state.matter).digest,
        )
    assert _retirements().count() == 1
    assert not LovableSyncState.objects.exists()
    # Until the replacement is enrolled, the night still stops on the size.
    run = _run(active, at=NIGHT + HOUR)
    assert run.status == SyncRunStatus.FAILED and run.error.startswith("COHORT_SIZE")
    assert EvidencePilotRecord.objects.filter(pilot=OPERATIONAL_COHORT).exists()


# ---------------------------------------------------------------------------
# 6. Below the command layer: the model and the database refuse a forgery
#    (QA 2026-11, C-L-04 defect 5)
# ---------------------------------------------------------------------------


def _forge(matter: Any, event: Any) -> Any:
    return _retirements().create(
        cohort=OPERATIONAL_COHORT,
        matter=matter,
        deletion_event=event,
        actor=None,
        reference="2026_512",
        kind="OPERATOR",
        retired_at=NIGHT,
        reason="forge",
    )


def test_a_retirement_of_a_live_teema_is_refused_by_the_model(active):
    """QA: `objects.create` accepted a live Teema; with a raw state removal beside
    it, the night passed with a member silently gone."""
    from app.legacy_import.lovable_sync_models import RETIREMENT_NEEDS_A_TOMBSTONE

    _enroll(active)
    live = _matter("2026_512")
    event = ChangeEvent.objects.filter(matter=live).first()
    assert event is not None
    with refused(RETIREMENT_NEEDS_A_TOMBSTONE):
        _forge(live, event)
    # An instance that only *claims* to be deleted proves nothing either.
    live.deleted_at = NIGHT
    with refused(RETIREMENT_NEEDS_A_TOMBSTONE):
        _forge(live, event)
    assert not _retirements().exists()
    # So the loss QA masked with the forged row is still caught as drift.
    _drift("2026_512")
    run = _run(active, at=NIGHT + HOUR)
    assert run.status == SyncRunStatus.FAILED and run.error.startswith("COHORT_SIZE")


def _tombstone_outside_every_cohort() -> Matter:
    """A Teema deleted through the service that held no state: no retirement."""
    outsider = factories.MatterFactory()
    permanently_delete_matter(
        matter=outsider,
        actor=factories.DepartmentHeadFactory(),
        confirmation=plan_matter_deletion(outsider).digest,
    )
    return Matter.all_objects.get(pk=outsider.pk)


def test_a_retirement_naming_another_event_is_refused_by_the_model(active):
    from app.audit.services import record_change_event
    from app.legacy_import.lovable_sync_models import RETIREMENT_NEEDS_ITS_DELETION_EVENT

    _enroll(active)
    member = _delete("2026_511")
    tomb = _tombstone_outside_every_cohort()
    assert _retirements().count() == 1
    wrong_type = record_change_event(
        event_type=ChangeEventType.MATTER_TITLE_CHANGED, matter=tomb, summary="muu sündmus"
    )
    another_teemas = _deletion_event(member)
    for event in (wrong_type, another_teemas):
        with refused(RETIREMENT_NEEDS_ITS_DELETION_EVENT):
            _forge(tomb, event)
    assert _retirements().count() == 1
    # The one shape still accepted: the tombstone beside its own deletion event.
    assert _forge(tomb, _deletion_event(tomb)).matter_id == tomb.pk


def test_the_database_refuses_an_update_or_delete_of_a_retirement(active):
    """QA: `QuerySet.update()` changed a row and `.delete()` removed one, since
    neither calls the model. Migration 0023's trigger refuses both."""
    _enroll(active)
    _delete("2026_511")
    row = _retirements().get()
    with pytest.raises(DatabaseError, match="does not permit UPDATE"), transaction.atomic():
        _retirements().filter(pk=row.pk).update(reason="muudetud")
    with pytest.raises(DatabaseError, match="does not permit DELETE"), transaction.atomic():
        _retirements().filter(pk=row.pk).delete()
    with pytest.raises(DatabaseError, match="does not permit UPDATE"), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE legacy_import_lovablesyncretirement SET cohort = 'x' WHERE id = %s",
                [row.pk],
            )
    kept = _retirements().get()
    assert (kept.pk, kept.reason, kept.cohort) == (row.pk, row.reason, row.cohort)
    # And the night still counts it.
    assert _run(active, at=NIGHT + HOUR).status == SyncRunStatus.SUCCEEDED


def test_deletion_and_retire_still_write_their_retirements(active):
    """The two doors pass the model's check: the tombstone is marked with
    `update()` before the event and the retirement are written."""
    from app.legacy_import import lovable_sync_retirement

    _enroll(active)
    tomb = _delete("2026_510")
    assert _retirements().get().deletion_event_id == _deletion_event(tomb).pk
    # An operator's retirement of a deletion made without one (the pre-fix shape).
    with mock.patch.object(lovable_sync_retirement, "record_for_deletion", return_value=[]):
        later = _delete("2026_512")
    assert _retirements().count() == 1
    plan = lovable_sync_retirement.retire(
        cohort=OPERATIONAL_COHORT, reference="2026_512", reason="Operaatori kanne", apply=True
    )
    assert plan.applied and _retirements().count() == 2
    assert _retirements().get(matter=later).deletion_event_id == _deletion_event(later).pk
    assert _run(active, at=NIGHT + HOUR).status == SyncRunStatus.SUCCEEDED
