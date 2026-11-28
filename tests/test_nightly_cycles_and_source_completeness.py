"""Expected cycles, recovery runs and source completeness (r98 final corrections, item 7)."""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core import integration_status, nightly_outcome, nightly_schedule
from app.core.models import NightlyStepRecord
from tests.admin_session import apply_admin_gate, full_admin, technical_admin, verified

pytestmark = pytest.mark.django_db
D = dt.date


@pytest.fixture(autouse=True)
def _gate(settings):
    return apply_admin_gate(settings)


def _record(day: dt.date, step: str, outcome: str, *, manual: bool = False, hour: int = 0):
    start = nightly_schedule.cycle_start_on(day) + dt.timedelta(hours=hour)
    nightly_outcome.remember(
        nightly_outcome.StepOutcome(step, outcome, "", {"n": 1}),
        rc=0,
        seconds=60,
        cycle_start=start,
        manual=manual,
    )


def _full_night(day: dt.date, outcome: str = nightly_outcome.SUCCEEDED):
    for step in nightly_outcome.STEPS:
        _record(day, step, outcome)


def _at(day: dt.date, hours: int) -> dt.datetime:
    return nightly_schedule.cycle_start_on(day) + dt.timedelta(hours=hours)


def test_an_entirely_missing_night_is_listed_between_recorded_nights():
    _full_night(D(2026, 11, 20))
    _full_night(D(2026, 11, 22))
    nights = integration_status.history(30, now=_at(D(2026, 11, 23), 8)).nights
    states = {n.day: n.state for n in nights}
    assert states[D(2026, 11, 21)] == integration_status.MISSING
    assert states[D(2026, 11, 20)] == states[D(2026, 11, 22)] == integration_status.COMPLETED
    assert states[D(2026, 11, 23)] == integration_status.MISSING  # due, window closed, no record


def test_nothing_before_the_first_record_is_reconstructed():
    _full_night(D(2026, 11, 20))
    days = [n.day for n in integration_status.history(30, now=_at(D(2026, 11, 21), 8)).nights]
    assert min(days) == D(2026, 11, 20)


def test_before_any_record_there_is_no_history_only_the_next_scheduled_start():
    found = integration_status.history(30, now=_at(D(2026, 11, 21), 8))
    assert found.nights == [] and found.recording_since is None
    assert found.next_start == nightly_schedule.cycle_start_on(D(2026, 11, 22))


def test_a_cycle_inside_its_window_is_running_not_missing_and_a_future_one_is_absent():
    _record(D(2026, 11, 21), "lovable", nightly_outcome.SUCCEEDED)
    (night,) = integration_status.history(30, now=_at(D(2026, 11, 21), 2)).nights
    assert night.state == integration_status.RUNNING
    assert integration_status.history(30, now=_at(D(2026, 11, 21), -1)).nights == []


@pytest.mark.parametrize(
    ("outcome", "state"),
    [
        (nightly_outcome.SUCCEEDED, integration_status.COMPLETED),
        (nightly_outcome.SUCCEEDED_NO_NEW, integration_status.COMPLETED),
        (nightly_outcome.PARTIAL, integration_status.PARTIAL),
        (nightly_outcome.FAILED, integration_status.FAILED),
        (nightly_outcome.INTERRUPTED, integration_status.FAILED),
        (nightly_outcome.SKIPPED_UNEXPECTED, integration_status.FAILED),
        (nightly_outcome.SKIPPED_DELIBERATE, integration_status.DISABLED),
    ],
)
def test_a_closed_cycle_is_labelled_by_its_steps(outcome, state):
    _full_night(D(2026, 11, 20), outcome)
    assert integration_status.history(30, now=_at(D(2026, 11, 20), 8)).nights[0].state == state


def test_a_closed_cycle_with_a_step_missing_is_partial():
    for step in nightly_outcome.STEPS[:-1]:
        _record(D(2026, 11, 20), step, nightly_outcome.SUCCEEDED)
    night = integration_status.history(30, now=_at(D(2026, 11, 20), 8)).nights[0]
    assert night.state == integration_status.PARTIAL
    assert [s.step for s in night.steps if s.missing] == [nightly_outcome.STEPS[-1]]


def test_a_manual_recovery_never_overwrites_the_scheduled_verdict():
    day = D(2026, 11, 20)
    _record(day, "register-watch", nightly_outcome.FAILED)
    _record(day, "register-watch", nightly_outcome.SUCCEEDED, manual=True, hour=9)
    rows = NightlyStepRecord.objects.filter(step="register-watch")
    assert rows.count() == 2
    assert rows.get(manual=False).outcome == nightly_outcome.FAILED
    night = integration_status.history(30, now=_at(day, 10)).nights[0]
    watch = next(s for s in night.steps if s.step == "register-watch")
    assert watch.record is not None and watch.record.outcome == nightly_outcome.FAILED
    assert [r.outcome for r in night.recoveries] == [nightly_outcome.SUCCEEDED]


def test_a_night_with_only_a_manual_run_is_still_a_missing_scheduled_cycle():
    _full_night(D(2026, 11, 18))
    _record(D(2026, 11, 19), "lovable", nightly_outcome.SUCCEEDED, manual=True, hour=7)
    nights = integration_status.history(30, now=_at(D(2026, 11, 20), 8)).nights
    night = next(n for n in nights if n.day == D(2026, 11, 19))
    assert night.state == integration_status.MISSING and len(night.recoveries) == 1


def test_the_page_labels_missing_cycles_recoveries_and_the_approximate_ai_column(client):
    _full_night(D(2026, 11, 18))
    _record(D(2026, 11, 20), "lovable", nightly_outcome.FAILED)
    _record(D(2026, 11, 20), "lovable", nightly_outcome.SUCCEEDED, manual=True, hour=9)
    tech, secret = technical_admin()
    full_admin()
    verified(client, tech, secret)
    page = client.get(reverse("haldus:nightly_history")).content.decode()
    assert "Tsükkel puudub täielikult" in page  # 19.11 left nothing
    assert "Käsitsi taastatud" in page and "ligikaudu" in page


def _watch_run(errors, when):
    from app.register_watch.models import WatchRun

    return WatchRun.objects.create(
        started_at=when - dt.timedelta(minutes=5),
        finished_at=when,
        errors=errors,
        targets_checked=9,
    )


def test_a_run_that_finished_with_source_errors_does_not_make_the_source_fresh(settings):
    settings.REGISTER_WATCH_FETCH = True
    now = timezone.now()
    _watch_run([{"allikas": "x", "viga": "timeout"}], now - dt.timedelta(hours=2))
    watch = next(s for s in integration_status.sources(now) if s.key == "register-watch")
    assert watch.last_completed is not None and watch.last_success is None
    assert watch.state != integration_status.OK
    assert any("vigadeta" in line for line in watch.limits)


def test_a_clean_run_is_the_only_one_that_sets_last_success(settings):
    settings.REGISTER_WATCH_FETCH = True
    now = timezone.now()
    clean = _watch_run([], now - dt.timedelta(hours=30))
    _watch_run([{"allikas": "x"}], now - dt.timedelta(hours=2))
    watch = next(s for s in integration_status.sources(now) if s.key == "register-watch")
    assert watch.last_success == clean.finished_at
    assert watch.last_completed is not None and watch.last_completed > watch.last_success
    assert watch.state == integration_status.ATTENTION and "osaline" in watch.line


def test_the_sweep_follows_the_same_rule(settings):
    from app.register_watch.models import SweepRun

    settings.REGISTER_SWEEP = True
    now = timezone.now()
    SweepRun.objects.create(
        started_at=now - dt.timedelta(hours=3),
        finished_at=now - dt.timedelta(hours=2),
        errors=[{"x": 1}],
    )
    sweep = next(s for s in integration_status.sources(now) if s.key == "register-sweep")
    assert sweep.last_success is None and sweep.last_completed is not None
    assert sweep.state == integration_status.ATTENTION
