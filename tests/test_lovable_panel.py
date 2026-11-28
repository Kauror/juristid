"""Haldus → Lovable: the diagnostics panel and the independent-verification panel (r98 final).

Synthetic records only. The two things the page must never blur are tested: synchronisation
success is not verification, and a clean sample / an empty comparison / a held or unverified
difference is never a green «kooskõlas»."""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from app.legacy_import import lovable_diagnostics as diagnostics
from app.legacy_import import lovable_source, parity_fresh
from app.legacy_import.lovable_sync_models import (
    LovableParityReport,
    LovableSyncRun,
    SyncMode,
    SyncRunStatus,
    SyncTrigger,
)
from tests.admin_session import apply_admin_gate, full_admin, technical_admin, verified

pytestmark = pytest.mark.django_db
NOW = timezone.now()


@pytest.fixture(autouse=True)
def _gate(settings):
    return apply_admin_gate(settings)


def _run(key, *, status=SyncRunStatus.SUCCEEDED, mode=SyncMode.APPLY, hours=2, **counts):
    started = NOW - dt.timedelta(hours=hours)
    return LovableSyncRun.objects.create(
        run_key=key,
        trigger=SyncTrigger.SCHEDULED,
        mode=mode,
        status=status,
        started_at=started,
        finished_at=started + dt.timedelta(minutes=5),
        snapshot_taken_at=started,
        mapping_version="x",
        backup_set="sets/20261128T000000Z",
        verified=True,
        counts=counts,
    )


def _report(**kw):
    values = {
        "at": NOW - dt.timedelta(hours=1),
        "mode": "full",
        "source": "snapshot",
        "snapshot_taken_at": NOW - dt.timedelta(hours=2),
        "snapshot_sha256": "a" * 64,
        "run_key": "k1",
        "teemad": 30,
        "eligible": 30,
        "counts": {
            "MATCH": 100,
            "EXPLAINED_DIFFERENCE": 5,
            "HELD": 0,
            "MISMATCH": 0,
            "UNVERIFIED": 0,
        },
    }
    values.update(kw)
    return LovableParityReport.objects.create(**values)


def _view():
    (view,) = diagnostics.collect(NOW).parity
    return view


# -- the verdict matrix ---------------------------------------------------------------------


def test_only_a_complete_current_clean_full_comparison_is_green():
    _run("k1")
    _report()
    view = _view()
    assert (view.status, view.currency) == (diagnostics.VERIFIED, diagnostics.CURRENT)


def test_a_clean_sample_is_not_register_parity():
    _run("k1")
    _report(mode="sample", sample_size=10, seed="s")
    view = _view()
    assert view.status == diagnostics.CLEAN_SAMPLE and view.currency == diagnostics.INCOMPLETE
    assert any("valim" in r for r in view.reasons) and "10 / 30" in view.scope


def test_an_empty_comparison_is_never_a_success():
    _report(
        teemad=0,
        counts={"MATCH": 0, "EXPLAINED_DIFFERENCE": 0, "HELD": 0, "MISMATCH": 0, "UNVERIFIED": 0},
    )
    view = _view()
    assert view.status == diagnostics.EMPTY and view.currency == diagnostics.INCOMPLETE


def test_held_unverified_and_changed_never_disappear_into_green():
    counts = {"MATCH": 100, "EXPLAINED_DIFFERENCE": 0, "HELD": 2, "MISMATCH": 0, "UNVERIFIED": 0}
    _report(counts=counts, held=[["2099_1", "stage"], ["2099_1", "lifecycle"]])
    assert _view().status == diagnostics.HELD
    LovableParityReport.objects.all().delete()
    _report(counts={**counts, "HELD": 0, "UNVERIFIED": 3}, unverified=[["2099_9", "*"]])
    assert _view().status == diagnostics.UNVERIFIED
    LovableParityReport.objects.all().delete()
    _report(
        source="fresh",
        counts={**counts, "HELD": 0, "CHANGED_SINCE_SYNC": 1},
        changed_since_sync=[["2099_2", "title"]],
        source_changes={"changed": ["2099_2"]},
    )
    view = _view()
    assert view.status == diagnostics.CHANGED
    assert view.source_changes == [("muutus Lovable'is pärast sünkroonimist", 1)]


def test_a_mismatch_is_a_problem_even_when_everything_else_matches():
    _report(
        counts={"MATCH": 999, "EXPLAINED_DIFFERENCE": 0, "HELD": 0, "MISMATCH": 1, "UNVERIFIED": 0},
        mismatches=[["2099_3", "title"]],
    )
    assert _view().status == diagnostics.MISMATCH


def test_a_comparison_that_could_not_be_made_is_not_made_and_says_why():
    _report(
        source="fresh",
        teemad=0,
        error="INCONSISTENT",
        counts={"MATCH": 0, "EXPLAINED_DIFFERENCE": 0, "HELD": 0, "MISMATCH": 0, "UNVERIFIED": 30},
    )
    view = _view()
    assert view.status == diagnostics.NOT_MADE and view.currency == diagnostics.INCOMPLETE
    assert any("muutus lugemise ajal" in r for r in view.reasons)


def test_a_comparison_older_than_the_latest_reading_is_outdated_not_current():
    old = _run("k1", hours=30)
    _report(snapshot_taken_at=old.snapshot_taken_at, at=NOW - dt.timedelta(hours=29))
    _run("k2", hours=2)  # a newer successful reading exists
    view = _view()
    assert view.currency == diagnostics.OUTDATED and view.status == diagnostics.STALE_CLEAN


def test_a_fresh_comparison_goes_outdated_after_a_sync_or_36_hours():
    _run("k1", hours=1)
    _report(source="fresh", at=NOW - dt.timedelta(hours=3))
    assert _view().currency == diagnostics.OUTDATED  # the sync finished after it
    LovableParityReport.objects.all().delete()
    LovableSyncRun.objects.all().delete()
    _run("k0", hours=80)
    _report(source="fresh", at=NOW - dt.timedelta(hours=40))
    assert _view().currency == diagnostics.OUTDATED


def test_snapshot_and_fresh_comparisons_are_listed_apart():
    _report(source="snapshot")
    _report(source="fresh", at=NOW - dt.timedelta(minutes=5))
    sources = [v.report.source for v in diagnostics.collect(NOW).parity]
    assert sorted(sources) == ["fresh", "snapshot"]


# -- the run diagnostics --------------------------------------------------------------------


def test_the_latest_attempt_and_the_latest_success_are_shown_separately():
    _run(
        "good",
        hours=30,
        source_live=40,
        source_operational=35,
        checked=30,
        unchanged=28,
        changed_source=2,
        breaker_limit=100,
        changed_handovers=1,
        handover_limit=50,
        restore_limit=20,
    )
    _run(
        "bad",
        status=SyncRunStatus.FAILED,
        hours=2,
        failed=3,
        verify_failed=1,
        held_local_work=2,
        changed_source=95,
        breaker_limit=100,
    )
    diag = diagnostics.collect(NOW)
    assert diag.attempt.run_key == "bad" and diag.success.run_key == "good"
    limits = {limit.label: limit for limit in diag.limits}
    assert limits["Tavaline piir: muudetud teemasid"].level == "near"
    problems = dict(diag.problems)
    assert problems["Ebaõnnestumised"] == 3 and problems["Tagasilugemise ebaõnnestumised"] == 1


def test_a_compare_only_night_is_said_to_be_compare_only():
    _run("a", mode=SyncMode.APPLY, hours=30)
    LovableSyncRun.objects.create(
        run_key="c",
        trigger=SyncTrigger.SCHEDULED,
        mode=SyncMode.COMPARE,
        status=SyncRunStatus.SUCCEEDED,
        started_at=NOW - dt.timedelta(hours=1),
        mapping_version="x",
    )
    assert diagnostics.collect(NOW).scheduled_compare_only


# -- the page -------------------------------------------------------------------------------


@pytest.fixture
def admin(client):
    tech, secret = technical_admin()
    full_admin()
    verified(client, tech, secret)
    return client


def test_the_page_shows_both_panels_and_never_green_for_a_sample(admin):
    _run("k1", changed_source=3, breaker_limit=100)
    _report(mode="sample", sample_size=5, seed="x")
    page = admin.get(reverse("haldus:lovable")).content.decode()
    assert "Sünkroonimise diagnostika" in page and "Sõltumatu võrdlus" in page
    assert "ei tõenda kogu registri kooskõla" in page
    assert (
        'data-parity-status="clean_sample"' in page and 'data-parity-currency="incomplete"' in page
    )
    assert "Kogu register on kooskõlas" not in page


def test_the_page_without_any_verification_says_none_was_done(admin):
    page = admin.get(reverse("haldus:lovable")).content.decode()
    assert (
        "Sõltumatut võrdlust pole veel tehtud" in page and "Kogu register on kooskõlas" not in page
    )


def test_discrepancies_are_listed_with_links_to_the_existing_workflows(admin):
    _report(
        counts={"MATCH": 10, "EXPLAINED_DIFFERENCE": 0, "HELD": 1, "MISMATCH": 1, "UNVERIFIED": 0},
        mismatches=[["2099_3", "title"]],
        held=[["2099_4", "stage"]],
    )
    page = admin.get(reverse("haldus:lovable")).content.decode()
    assert "2099_3" in page and "pealkiri" in page and "2099_4" in page
    assert reverse("haldus:data_sync_differences") in page
    assert reverse("haldus:data_sync_release_hold") + "?viide=2099_4" in page
    # a Teema the reader may not open is named by reference only, never linked or described
    assert "/teemad/" not in page.split("data-lovable-parity")[1].split("</section>")[0]


def test_the_page_reads_no_source_and_writes_nothing(admin, monkeypatch):
    monkeypatch.setattr(parity_fresh, "run_fresh", lambda *a, **k: pytest.fail("fresh read"))
    monkeypatch.setattr(lovable_source, "HttpTransport", lambda *a, **k: pytest.fail("transport"))
    before = (LovableSyncRun.objects.count(), LovableParityReport.objects.count())
    assert admin.get(reverse("haldus:lovable")).status_code == 200
    assert (LovableSyncRun.objects.count(), LovableParityReport.objects.count()) == before


def test_the_page_is_refused_to_everyone_but_the_technical_administrator(client):
    assert client.get(reverse("haldus:lovable")).status_code in (302, 404)
