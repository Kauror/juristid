# ruff: noqa: F811 - the fixtures imported from fixtures.py are used as arguments
"""Operator-triggered fresh-read parity (r98 final, item 2): synthetic world only.

What is proved: a fresh reading is taken only through read-only GETs; snapshot parity and
current-source parity are kept apart; a Teema edited in Lovable since the night is a legitimate
source change, not a defect; new, reopened, retired, held and missing Teemad are classified;
a reading that cannot be made consistent (or Juristid moving underneath it) leaves the
comparison UNVERIFIED and is never green; the command is the only way to start it."""

from __future__ import annotations

import datetime as dt
import io

import pytest
from django.core.management import call_command
from django.utils import timezone

from app.legacy_import import lovable_source, parity_fresh
from app.legacy_import.lovable_sync_models import (
    LovableParityReport,
    LovableSyncRun,
    SyncRunStatus,
    SyncTrigger,
)
from app.legacy_import.parity import verifier
from tests.lovable_contract.fixtures import pilot_on, world  # noqa: F401 - fixtures
from tests.lovable_contract.world import AVA, ref, source_id
from tests.lovable_contract.world import lovable_row as row

pytestmark = pytest.mark.django_db


def _fresh(world, **kw):
    moment = world.runs[-1].started_at + dt.timedelta(hours=2)
    return parity_fresh.run_fresh(world.lovable, now=lambda: moment, pause=lambda _s: None, **kw)


def _next_night(world):
    return world.run(
        trigger=SyncTrigger.SCHEDULED, at=world.runs[-1].started_at + dt.timedelta(days=1)
    )


@pytest.fixture
def night(world):
    world.run(trigger=SyncTrigger.SCHEDULED)
    return world


def test_a_converged_register_is_clean_by_a_fresh_reading_and_says_so(night):
    report = _fresh(night)
    assert report.source == "fresh" and report.mode == "full" and report.complete
    assert report.clean and report.teemad == 3 and report.sample_size is None
    assert report.eligible == 3 and report.source_changes == {}
    stored = LovableParityReport.objects.get()
    assert stored.source == "fresh" and stored.run_key == night.runs[-1].run_key
    assert stored.snapshot_taken_at is not None and stored.error == ""


def test_the_fresh_reading_uses_get_only(night):
    before = len(night.lovable.calls)
    _fresh(night)
    new = night.lovable.calls[before:]
    assert new and {method for method, _ in new} == {"GET"}
    assert night.lovable.writes == []
    assert {table for _, table in new} <= set(lovable_source.TABLES)


def test_a_source_edit_since_the_night_is_a_legitimate_change_not_a_defect(night):
    night.lovable.edit(ref(2), teema="Lovable'is hiljem muudetud pealkiri")
    report = _fresh(night)
    assert (ref(2), "title") in report.changed_since_sync
    assert report.mismatches == [] and report.counts[verifier.CHANGED_SINCE_SYNC] >= 1
    assert report.source_changes == {"changed": [ref(2)]}
    assert not report.clean  # an unexplained-by-sync difference is never green either
    # …while the stored reading, which is the night's, still agrees with Juristid:
    assert LovableParityReport.objects.filter(source="fresh").count() == 1


def test_a_difference_on_the_juristid_side_with_an_unchanged_source_is_a_mismatch(night):
    matter = night.matter(ref(3))
    type(matter).all_objects.filter(pk=matter.pk).update(title="Ainult Juristides muudetud")
    report = _fresh(night)
    assert (ref(3), "title") in report.mismatches and report.changed_since_sync == []


def test_a_new_topic_in_lovable_is_new_since_sync_and_unverified_not_matched(night):
    new = ref(9)
    night.lovable.put(source_id(new), row(new), version=1)
    report = _fresh(night)
    assert report.source_changes.get("new") == [new]
    assert (new, "*") in report.unverified and not report.clean


def test_a_retired_topic_is_gone_and_unverified(night):
    night.lovable.soft_delete(ref(3))
    report = _fresh(night)
    assert report.source_changes.get("gone") == [ref(3)]
    assert (ref(3), "*") in report.unverified and not report.clean


def test_a_reopened_topic_is_detected_as_reopened(night):
    night.lovable.edit(ref(1), hetkeseis="jõustunud", jargmiseks="", tegevuse_tahtaeg=None)
    _next_night(night)  # the closure is synchronised…
    assert not night.matter(ref(1)).is_open
    night.lovable.edit(
        ref(1), hetkeseis="Riigikogus", jargmiseks="Ootan uuesti", tegevuse_tahtaeg="2099-07-01"
    )
    report = _fresh(night)  # …and Lovable reopens it afterwards
    assert report.source_changes.get("reopened") == [ref(1)]
    assert ref(1) in report.source_changes.get("changed", [])


def test_a_closure_held_for_juristid_work_is_held_never_green(night):
    from app.matters.services import plan_website_overview

    plan_website_overview(matter=night.matter(ref(1)), actor=night.person(AVA))
    night.lovable.edit(ref(1), hetkeseis="jõustunud", jargmiseks="", tegevuse_tahtaeg=None)
    _next_night(night)
    report = _fresh(night)
    assert report.held and all(reference == ref(1) for reference, _ in report.held)
    assert not report.clean and report.mismatches == []


def test_a_missing_record_is_unverified(night):
    report = _fresh(night, sample=0)
    assert report.clean
    sample = parity_fresh.run_fresh(
        night.lovable,
        now=lambda: night.runs[-1].started_at + dt.timedelta(hours=2),
        pause=lambda _s: None,
        sample=2,
        seed="x",
    )
    assert sample.mode == "sample" and sample.sample_size == 2 and sample.eligible == 3
    assert not sample.complete  # a clean sample is not register parity
    assert sample.teemad == 2


def test_a_sample_is_reproducible_with_its_seed(night):
    one = _fresh(night, sample=2, seed="a")
    two = _fresh(night, sample=2, seed="a")
    assert (one.counts, one.teemad, one.seed) == (two.counts, two.teemad, two.seed == "a" and "a")
    kept = LovableParityReport.objects.filter(seed="a", sample_size=2)
    assert kept.count() == 2 and all(r.mode == "sample" and r.eligible == 3 for r in kept)


def test_an_unreadable_source_leaves_everything_unverified_and_never_clean(night):
    night.lovable.fail = lovable_source.SourceError("HTTP_503", "unavailable")
    report = _fresh(night)
    assert report.error == "HTTP_503" and not report.clean and not report.complete
    assert report.counts[verifier.UNVERIFIED] == 3 and report.counts[verifier.MATCH] == 0
    stored = LovableParityReport.objects.get()
    assert stored.error == "HTTP_503" and stored.counts["MATCH"] == 0


def test_a_source_that_keeps_changing_while_it_is_read_is_unverified(night):
    state = {"n": 0}

    def churn(table):
        state["n"] += 1
        if table == "topic" and state["n"] > 2:
            night.lovable.edit(ref(1), teema=f"muutub {state['n']}")

    night.lovable.on_select = churn
    report = _fresh(night)
    assert report.error == "INCONSISTENT" and not report.clean
    assert report.counts[verifier.MATCH] == 0


def test_a_synchronisation_running_blocks_the_comparison(night):
    run = LovableSyncRun.objects.first()
    LovableSyncRun.objects.filter(pk=run.pk).update(status=SyncRunStatus.RUNNING)
    report = _fresh(night)
    assert report.error == "SYNC_RUNNING" and not report.clean


def test_a_synchronisation_finishing_during_the_comparison_makes_it_unverified(night):
    def sync_now(table):
        LovableSyncRun.objects.update(updated_at=timezone.now())  # Juristid moved underneath
        night.lovable.on_select = None

    night.lovable.on_select = sync_now
    report = _fresh(night)
    assert report.error == "SYNC_DURING_COMPARISON" and not report.clean


def test_the_command_runs_fresh_only_when_asked_and_exits_nonzero_on_a_difference(
    night, monkeypatch
):
    monkeypatch.setattr(lovable_source, "load_config", lambda path=None: object())
    monkeypatch.setattr(lovable_source, "HttpTransport", lambda config: night.lovable)
    night.lovable.edit(ref(2), teema="Lovable'is muudetud")
    out = io.StringIO()
    with pytest.raises(SystemExit) as stopped:
        call_command("lovable_parity", "--fresh", stdout=out)
    assert stopped.value.code == 1
    assert "fresh reading" in out.getvalue() and "CHANGED_SINCE_SYNC" in out.getvalue()
    assert LovableParityReport.objects.filter(source="fresh").count() == 1
    # the default mode never touches the transport
    monkeypatch.setattr(lovable_source, "HttpTransport", lambda config: pytest.fail("fresh read"))
    calls = len(night.lovable.calls)
    with pytest.raises(SystemExit):
        call_command("lovable_parity", stdout=io.StringIO())
    assert len(night.lovable.calls) == calls
