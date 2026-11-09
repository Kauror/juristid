# ruff: noqa: F811 - pytest fixtures imported from the sync suites are used as arguments
"""REL-01 hardening after the independent review of 752e052b.

* P3-1: an unproven membership at deletion is logged with the Teema's id and
  the exact reason, and Lovable ids are compared as UUIDs, not as strings.
* P3-3: migration 0024 is not reversed while a membership or a retirement
  exists; on empty tables it reverses as before.
* P3-5: the nightly seeding never seeds a permanently deleted Teema, so a
  deletion racing a night cannot fail it at the membership trigger.
"""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace

import pytest
from django.db import DatabaseError, connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from app.legacy_import import lovable_sync, lovable_sync_retirement
from app.legacy_import.lovable_sync import OPERATIONAL_COHORT, PILOT_COHORT
from app.legacy_import.lovable_sync_models import (
    LovableSyncMembership,
    LovableSyncRetirement,
    LovableSyncState,
)
from app.legacy_import.lovable_sync_retirement import (
    MEMBERSHIP_ID_MISMATCH,
    NOT_ONE_REFERENCE,
    PROVENANCE_ID_MISMATCH,
    same_lovable_id,
)
from app.legacy_import.models import EvidencePilotRecord
from app.matters.models import Matter
from tests import factories
from tests.test_lovable_operational_cohort import HOUR, _enroll, active  # noqa: F401
from tests.test_lovable_pilot import pilot_on, world  # noqa: F401
from tests.test_lovable_sync import NIGHT, _matter, _run, cohort  # noqa: F401
from tests.test_rel01_retirement_membership import _delete, _enroll_as_before_0024

LOGGER = "app.legacy_import.lovable_sync_retirement"


# ---------------------------------------------------------------------------
# P3-1: which Teema, and why; ids compared as UUIDs
# ---------------------------------------------------------------------------


def test_lovable_ids_are_compared_as_uuids_not_as_spellings():
    one = uuid.UUID("0a1b2c3d-0000-4000-8000-00000000abcd")
    assert same_lovable_id(one, "0A1B2C3D-0000-4000-8000-00000000ABCD")
    assert same_lovable_id("{0a1b2c3d-0000-4000-8000-00000000abcd}", one)
    assert same_lovable_id(str(one), one)
    assert not same_lovable_id(one, "0a1b2c3d-0000-4000-8000-00000000abce")
    for not_an_id in (None, "", "x", 12):
        assert not same_lovable_id(one, not_an_id)


def test_provenance_naming_the_id_in_another_spelling_still_proves_it(monkeypatch):
    """A string compare would have called an upper-case id in the provenance a
    mismatch and left a real member unretired."""
    state_id = uuid.UUID("0a1b2c3d-0000-4000-8000-00000000abcd")
    matter_id = uuid.uuid4()
    reference = SimpleNamespace(
        matter_id=matter_id, source_row_raw={"lovable": {"id": str(state_id).upper()}}
    )
    monkeypatch.setattr(lovable_sync, "cohort_references", lambda _cohort: [reference])
    problem = lovable_sync_retirement._provenance_problem(object(), matter_id, state_id)
    assert problem == ""
    other = uuid.UUID("0a1b2c3d-0000-4000-8000-00000000abce")
    assert lovable_sync_retirement._provenance_problem(object(), matter_id, other) == (
        PROVENANCE_ID_MISMATCH
    )


@pytest.mark.django_db
def test_a_membership_id_mismatch_is_logged_with_the_teema_and_the_reason(active, caplog):
    _enroll(active)
    matter = _matter("2026_511")
    LovableSyncState.objects.filter(matter=matter).update(
        source_id="00000000-0000-0000-0000-0000000feed1"
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _delete("2026_511")
    assert not LovableSyncRetirement.objects.exists()
    [record] = [r for r in caplog.records if r.name == LOGGER]
    message = record.getMessage()
    assert str(matter.pk) in message and OPERATIONAL_COHORT in message
    assert MEMBERSHIP_ID_MISMATCH in message
    assert "Näidisteema" not in message  # identifiers only, never a title


@pytest.mark.django_db
def test_a_provenance_id_mismatch_is_logged_with_the_teema_and_the_reason(active, caplog):
    _enroll_as_before_0024(active)
    matter = _matter("2026_512")
    LovableSyncState.objects.filter(matter=matter).update(
        source_id="00000000-0000-0000-0000-0000000feed4"
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _delete("2026_512")
    assert not LovableSyncMembership.objects.filter(matter=matter).exists()
    assert not LovableSyncRetirement.objects.exists()
    [record] = [r for r in caplog.records if r.name == LOGGER]
    assert str(matter.pk) in record.getMessage()
    assert PROVENANCE_ID_MISMATCH in record.getMessage()


@pytest.mark.django_db
def test_a_member_without_one_canonical_reference_is_logged_with_the_count(active, caplog):
    _enroll_as_before_0024(active)
    matter = _matter("2026_510")
    # Its provenance gone before the deletion: no canonical reference names it.
    EvidencePilotRecord.objects.filter(matter=matter, record_kind__in=("MATTER", "REBASE")).delete()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _delete("2026_510")
    assert not LovableSyncRetirement.objects.exists()
    [record] = [r for r in caplog.records if r.name == LOGGER]
    assert str(matter.pk) in record.getMessage()
    assert f"{NOT_ONE_REFERENCE} (0)" in record.getMessage()
    # Unproven, so the night still judges the loss as drift.
    run = _run(active, at=NIGHT + HOUR)
    assert run.status == "FAILED" and run.error.startswith("COHORT_SIZE")


# ---------------------------------------------------------------------------
# P3-5: a tombstone is never seeded
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_nightly_seeding_skips_a_teema_deleted_after_its_references_were_listed(cohort):
    """The deletion committed between the night's read of the cohort's references
    and the seed: the Teema is a tombstone with its provenance still listed. On
    752e052b the membership trigger refused the seed and failed the night."""
    tomb = _matter("2026_503")
    Matter.all_objects.filter(pk=tomb.pk).update(deleted_at=timezone.now())
    states = lovable_sync.ensure_states()
    assert tomb.pk not in {s.matter_id for s in states}
    assert not LovableSyncState.objects.filter(matter=tomb).exists()
    assert not LovableSyncMembership.objects.filter(matter=tomb).exists()
    assert LovableSyncMembership.objects.filter(cohort=PILOT_COHORT).count() == 3


# ---------------------------------------------------------------------------
# P3-3: 0024 is not reversed over evidence
# ---------------------------------------------------------------------------

#: The graph's leaf: re-migrating to 0024 only would leave later tables unapplied.
LEAF = ("legacy_import", "0029_ls2_c_onenote_hold_decisions")
BEFORE = ("legacy_import", "0023_lovable_sync_retirement")


def _migrate(target: tuple[str, str]) -> None:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])


def _table_exists() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('legacy_import_lovablesyncmembership')")
        return cursor.fetchone()[0] is not None


@pytest.fixture
def back_at_the_leaf():
    yield
    if not _table_exists():
        _migrate(LEAF)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_0024_refuses_to_reverse_over_a_membership_and_reverses_when_empty(back_at_the_leaf):
    matter = factories.MatterFactory()
    state = LovableSyncState.objects.create(
        matter=matter,
        source_id="00000000-0000-0000-0000-0000000feed5",
        reference="2026_999",
        cohort=PILOT_COHORT,
    )
    lovable_sync.remember_membership(
        cohort=state.cohort, matter_id=matter.pk, source_id=state.source_id
    )
    with pytest.raises(DatabaseError, match="not reversed while cohort memberships"):
        _migrate(BEFORE)
    assert _table_exists() and LovableSyncMembership.objects.count() == 1
    # Emptied (only TRUNCATE can: the table is append-only), it reverses and re-applies.
    with connection.cursor() as cursor:
        cursor.execute("TRUNCATE legacy_import_lovablesyncmembership")
    _migrate(BEFORE)
    assert not _table_exists()
    _migrate(LEAF)
    assert _table_exists()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname IN "
            "('legacy_import_lovablesyncmembership_append_only', "
            "'legacy_import_lovablesyncmembership_evidenced', "
            "'legacy_import_lovablesyncretirement_evidenced')"
        )
        assert cursor.fetchone()[0] == 3
