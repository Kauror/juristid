# ruff: noqa: F811 - the fixtures imported from fixtures.py are used as arguments
"""The repeatable parity verifier (R98-12, ADR 0238): the independent oracle over a stored reading.

Synthetic world only. What is proved: a converged register verifies clean; a genuine
difference on the Juristid side is a MISMATCH; a Teema that cannot be compared is UNVERIFIED
(never a match); the verifier only reads; a sample is reproducible; the command keeps
counts and (reference, field) pairs, never a value.
"""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from app.legacy_import.lovable_sync_models import (
    LovableParityReport,
    LovableSyncSnapshot,
    SyncTrigger,
)
from app.legacy_import.parity import verifier
from tests.lovable_contract.fixtures import pilot_on, world  # noqa: F401 - fixtures
from tests.lovable_contract.world import ref

pytestmark = pytest.mark.django_db


def _scheduled(world):
    run = world.run(trigger=SyncTrigger.SCHEDULED)
    snapshot = LovableSyncSnapshot.objects.get(run=run)
    return run, verifier.view_from_snapshot(snapshot.document, snapshot.taken_at, snapshot.sha256)


def test_a_converged_register_verifies_clean_and_names_what_it_compared(world):
    _, view = _scheduled(world)
    report = verifier.verify(view, today=world.today)
    assert report.teemad == 3 and report.fields >= 3 * 10
    assert report.counts[verifier.MISMATCH] == 0 and report.clean
    assert report.counts[verifier.MATCH] > 0
    assert report.sha256 == view.sha256 != ""


def test_a_difference_on_the_juristid_side_is_a_mismatch(world):
    _, view = _scheduled(world)
    matter = world.matter(ref(2))
    type(matter).all_objects.filter(pk=matter.pk).update(title="Teisiti kirjutatud pealkiri")
    report = verifier.verify(view, today=world.today)
    assert (ref(2), "title") in report.mismatches and not report.clean
    assert all(reference == ref(2) for reference, _ in report.mismatches)


def test_a_teema_that_cannot_be_compared_is_unverified_never_a_match(world):
    _, view = _scheduled(world)
    report = verifier.verify(view, today=world.today, references=[ref(1), "2099_77"])
    assert ("2099_77", "*") in report.unverified
    assert not report.clean  # parity over nothing is no evidence


def test_a_value_juristid_cannot_represent_is_explained_not_matched(world):
    world.lovable.edit(ref(1), hetkeseis="mingi uus seis")  # a stage the contract does not map
    _, view = _scheduled(world)
    report = verifier.verify(view, today=world.today, references=[ref(1)])
    assert report.counts[verifier.EXPLAINED_DIFFERENCE] >= 1
    assert (ref(1), "stage") in report.explained


def test_verification_only_reads(world):
    _, view = _scheduled(world)
    with CaptureQueriesContext(connection) as captured:
        verifier.verify(view, today=world.today)
    writes = [
        q["sql"][:40]
        for q in captured.captured_queries
        if q["sql"].lstrip().split()[0] in {"INSERT", "UPDATE", "DELETE"}
    ]
    assert writes == []
    # and the source side is a plain value object: nothing here can reach Lovable
    assert not hasattr(view, "select")


def test_a_sample_is_reproducible_and_seed_dependent():
    population = [f"2099_{n}" for n in range(1, 41)]
    first = verifier.sample_of(population, 8, "a")
    assert first == verifier.sample_of(population, 8, "a") and len(first) == 8
    assert first != verifier.sample_of(population, 8, "b")


def test_the_command_records_counts_and_pairs_but_no_values(world):
    world.run(trigger=SyncTrigger.SCHEDULED)
    matter = world.matter(ref(3))
    type(matter).all_objects.filter(pk=matter.pk).update(title="Ainult siin nähtav pealkiri")
    out = io.StringIO()
    with pytest.raises(SystemExit) as stopped:
        call_command("lovable_parity", "--record", stdout=out)
    assert stopped.value.code == 1  # a mismatch exits non-zero
    stored = LovableParityReport.objects.get()
    assert [ref(3), "title"] in stored.mismatches and stored.counts["MISMATCH"] >= 1
    kept = f"{stored.counts} {stored.mismatches} {stored.held} {stored.unverified}"
    assert "Ainult siin nähtav pealkiri" not in kept


def test_the_command_refuses_when_there_is_no_reading(world):
    LovableSyncSnapshot.objects.all().delete()
    with pytest.raises(SystemExit) as stopped:
        call_command("lovable_parity", stderr=io.StringIO())
    assert stopped.value.code == 2
