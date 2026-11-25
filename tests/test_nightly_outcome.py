"""`app.core.nightly_outcome` and `manage.py nightly_outcome` (ADR 0229 §4).

An exit code of 0 is not success: each step of the nightly cycle is classified
from its own run record. Synthetic records only.
"""

from __future__ import annotations

import datetime as dt
import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from app.core import nightly_outcome as no
from app.core import nightly_schedule
from app.legacy_import import lovable_sync
from app.legacy_import.lovable_sync_models import (
    LovableSyncControl,
    LovableSyncRun,
    SyncMode,
    SyncRunStatus,
    SyncTrigger,
)
from app.register_watch.models import SweepRun, WatchRun

pytestmark = pytest.mark.django_db

UTC = dt.UTC


@pytest.fixture
def since() -> dt.datetime:
    return timezone.now() - dt.timedelta(minutes=5)


def _sync_run(since: dt.datetime, status: str, **counts: int) -> LovableSyncRun:
    return LovableSyncRun.objects.create(
        run_key=f"k-{status}-{len(counts)}",
        trigger=SyncTrigger.SCHEDULED,
        mode=SyncMode.APPLY,
        status=status,
        started_at=since + dt.timedelta(seconds=30),
        finished_at=None if status == SyncRunStatus.RUNNING else since + dt.timedelta(minutes=3),
        mapping_version="test",
        counts=counts,
        error="BREAKER over the limit" if status == SyncRunStatus.REVIEW_ONLY else "",
    )


def _control(mode: str = SyncMode.APPLY, cut_over: bool = False) -> None:
    LovableSyncControl.objects.update_or_create(pk=1, defaults={"mode": mode})
    if cut_over:
        LovableSyncControl.objects.filter(pk=1).update(cut_over=True, mode=SyncMode.OFF)


# -- lovable -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "counts", "expected"),
    [
        (SyncRunStatus.SUCCEEDED, {"updated": 3}, no.SUCCEEDED),
        (SyncRunStatus.SUCCEEDED, {"checked": 2300}, no.SUCCEEDED_NO_NEW),
        (SyncRunStatus.SUCCEEDED, {"updated": 3, "failed": 1}, no.PARTIAL),
        (SyncRunStatus.REVIEW_ONLY, {"checked": 2300}, no.PARTIAL),
        (SyncRunStatus.FAILED, {}, no.FAILED),
        (SyncRunStatus.RUNNING, {}, no.INTERRUPTED),
    ],
)
def test_the_lovable_step_is_read_from_its_run(since, status, counts, expected):
    _control()
    _sync_run(since, status, **counts)
    assert no.classify("lovable", since=since, rc=0).outcome == expected


def test_a_lovable_exit_zero_with_no_run_is_unexpected(since):
    """The script exits 0 when another sync held the lock or a release did."""
    _control()
    result = no.classify("lovable", since=since, rc=0)
    assert result.outcome == no.SKIPPED_UNEXPECTED
    assert "no run record" in result.reason


def test_a_lovable_sync_switched_off_or_cut_over_is_deliberate(since):
    _control(SyncMode.OFF)
    assert no.classify("lovable", since=since, rc=0).outcome == no.SKIPPED_DELIBERATE
    _control(cut_over=True)
    result = no.classify("lovable", since=since, rc=0)
    assert result.outcome == no.SKIPPED_DELIBERATE and "cut over" in result.reason


def test_a_night_that_already_ran_is_deliberate(since):
    from app.legacy_import import lovable_sync

    _control()
    LovableSyncRun.objects.create(
        run_key=lovable_sync.run_key_for(SyncTrigger.SCHEDULED, timezone.now()),
        trigger=SyncTrigger.SCHEDULED,
        mode=SyncMode.APPLY,
        status=SyncRunStatus.SUCCEEDED,
        started_at=since - dt.timedelta(hours=1),
        mapping_version="test",
    )
    assert no.classify("lovable", since=since, rc=0).outcome == no.SKIPPED_DELIBERATE


def test_a_lovable_failure_exit_with_no_run_is_failed(since):
    _control()
    assert no.classify("lovable", since=since, rc=1).outcome == no.FAILED


# -- register watch and sweep ---------------------------------------------------------


def _watch(since: dt.datetime, **fields) -> WatchRun:
    defaults = {
        "started_at": since + dt.timedelta(seconds=10),
        "finished_at": since + dt.timedelta(minutes=12),
        "targets_checked": 300,
    }
    defaults.update(fields)
    return WatchRun.objects.create(**defaults)


@override_settings(REGISTER_WATCH_FETCH=True)
@pytest.mark.parametrize(
    ("fields", "rc", "expected"),
    [
        ({"findings_new": 4}, 0, no.SUCCEEDED),
        ({}, 0, no.SUCCEEDED_NO_NEW),
        # Source errors with exit 0: the register that could not be read is not success.
        ({"errors": [{"viga": "riigikogu 503"}]}, 0, no.PARTIAL),
        ({"ai_failures": 1}, 0, no.PARTIAL),
        ({"errors": [{"viga": "x"}], "targets_checked": 0}, 0, no.FAILED),
        ({"errors": [{"viga": "Kontroll katkes", "katkes": True}]}, 1, no.FAILED),
        ({"finished_at": None}, 0, no.INTERRUPTED),
        ({"findings_new": 4}, 1, no.FAILED),
    ],
)
def test_the_watch_is_read_from_its_run(since, fields, rc, expected):
    _watch(since, **fields)
    assert no.classify("register-watch", since=since, rc=rc).outcome == expected


@override_settings(REGISTER_WATCH_FETCH=True)
def test_a_watch_with_no_run_is_unexpected_or_failed_and_names_a_held_lock(since):
    assert no.classify("register-watch", since=since, rc=0).outcome == no.SKIPPED_UNEXPECTED
    assert no.classify("register-watch", since=since, rc=1).outcome == no.FAILED
    WatchRun.objects.create(started_at=since - dt.timedelta(minutes=30))  # still going
    held = no.classify("register-watch", since=since, rc=1)
    assert held.outcome == no.SKIPPED_UNEXPECTED and "lock" in held.reason


@override_settings(REGISTER_WATCH_FETCH=False)
def test_a_watch_switched_off_is_deliberate(since):
    assert no.classify("register-watch", since=since, rc=0).outcome == no.SKIPPED_DELIBERATE


@override_settings(REGISTER_WATCH_FETCH=True, REGISTER_SWEEP=True)
def test_the_sweep_is_read_from_its_run(since):
    SweepRun.objects.create(
        started_at=since + dt.timedelta(seconds=10),
        finished_at=since + dt.timedelta(minutes=1),
        sources_read=9,
        candidates_new=0,
        errors=[{"viga": "pdr 500"}],
    )
    result = no.classify("register-sweep", since=since, rc=0)
    assert result.outcome == no.PARTIAL
    assert result.counts == {"read": 9, "new": 0, "errors": 1, "ai_failures": 0}


@override_settings(REGISTER_WATCH_FETCH=True, REGISTER_SWEEP=False)
def test_a_sweep_switched_off_is_deliberate(since):
    assert no.classify("register-sweep", since=since, rc=0).outcome == no.SKIPPED_DELIBERATE


# -- agenda ----------------------------------------------------------------------------


@override_settings(REGISTER_WATCH_FETCH=True)
@pytest.mark.parametrize(
    ("reported", "rc", "expected"),
    [
        ({"stored": 1, "judged": 25, "errors": 0, "ai_failures": 0}, 0, no.SUCCEEDED),
        ({"stored": 0, "judged": 0, "errors": 0, "ai_failures": 0}, 0, no.SUCCEEDED_NO_NEW),
        ({"stored": 0, "judged": 0, "errors": 2, "ai_failures": 0}, 0, no.PARTIAL),
        ({"stored": 1, "judged": 0, "errors": 0, "ai_failures": 1}, 0, no.PARTIAL),
        (None, 1, no.FAILED),
        (None, 0, no.SKIPPED_UNEXPECTED),
    ],
)
def test_the_agenda_is_read_from_its_machine_line(since, reported, rc, expected):
    result = no.classify("government-agenda", since=since, rc=rc, reported=reported)
    assert result.outcome == expected


@override_settings(REGISTER_WATCH_FETCH=False)
def test_an_agenda_switched_off_is_deliberate(since):
    assert no.classify("government-agenda", since=since, rc=1).outcome == no.SKIPPED_DELIBERATE


@override_settings(RIIGIKOGU_MONITORING=False)
def test_riigikogu_switched_off_is_a_deliberate_skip(since):
    # With PR #723 integrated the step reads its own `StageRun` records instead of
    # its exit code; every case is in tests/test_r93_integration.py.
    off = no.classify("riigikogu", since=since, rc=0)
    assert off.outcome == no.SKIPPED_DELIBERATE


# -- the command -----------------------------------------------------------------------


@override_settings(REGISTER_WATCH_FETCH=True)
def test_the_command_prints_one_machine_line(since):
    _watch(since, findings_new=2)
    out = StringIO()
    call_command(
        "nightly_outcome",
        "--step",
        "register-watch",
        "--since",
        since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "--rc",
        "0",
        stdout=out,
    )
    line = out.getvalue().strip()
    assert line.startswith(no.PREFIX)
    payload = json.loads(line.removeprefix(no.PREFIX))
    assert payload["step"] == "register-watch" and payload["outcome"] == no.SUCCEEDED


def test_the_command_refuses_a_moment_without_an_offset():
    with pytest.raises(CommandError, match="offset"):
        call_command(
            "nightly_outcome", "--step", "lovable", "--since", "2026-11-25T22:00:00", "--rc", "0"
        )


def test_the_command_refuses_reported_counts_that_are_not_an_object():
    with pytest.raises(CommandError, match="not a JSON object"):
        call_command(
            "nightly_outcome",
            "--step",
            "government-agenda",
            "--since",
            "2026-11-25T22:00:00Z",
            "--rc",
            "0",
            "--reported",
            "[1]",
        )


# -- the schedule ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("moment", "start"),
    [
        # Winter: 00:00 Tallinn is 22:00Z the day before.
        (
            dt.datetime(2026, 11, 25, 10, 0, tzinfo=UTC),
            dt.datetime(2026, 11, 25, 22, 0, tzinfo=UTC),
        ),
        # Exactly at a cycle's start: that cycle.
        (
            dt.datetime(2026, 11, 25, 22, 0, tzinfo=UTC),
            dt.datetime(2026, 11, 25, 22, 0, tzinfo=UTC),
        ),
        # Summer: 21:00Z.
        (dt.datetime(2026, 7, 1, 21, 30, tzinfo=UTC), dt.datetime(2026, 7, 2, 21, 0, tzinfo=UTC)),
        # Clocks forward on 29.03.2026 at 01:00Z: the next cycle is at UTC+3.
        (dt.datetime(2026, 3, 28, 23, 0, tzinfo=UTC), dt.datetime(2026, 3, 29, 21, 0, tzinfo=UTC)),
        # Clocks back on 25.10.2026 at 01:00Z: the next cycle is at UTC+2.
        (
            dt.datetime(2026, 10, 24, 22, 0, tzinfo=UTC),
            dt.datetime(2026, 10, 25, 22, 0, tzinfo=UTC),
        ),
    ],
)
def test_the_next_cycle_is_midnight_in_tallinn_across_the_seasons(moment, start):
    assert nightly_schedule.next_cycle_start(moment) == start


def test_each_step_is_due_after_the_steps_before_it():
    by = nightly_schedule.FINISH_BY
    assert by["lovable"] < by["government-agenda"] < by["register-watch"] < by["register-sweep"]
    assert nightly_schedule.CYCLE_COMPLETE_BY == by["register-sweep"]


# -- a person's recovery run (`juristid-nightly-cycle.sh --manual`) --------------------


def test_a_manual_recovery_is_classified_from_its_manual_run(since):
    """The recovery runs the sync with --manual, which records trigger MANUAL. Looking
    only for SCHEDULED reported a successful recovery as «no run record»."""
    _control()
    run = _sync_run(since, SyncRunStatus.SUCCEEDED, checked=40, updated=3)
    run.trigger = SyncTrigger.MANUAL
    run.run_key = lovable_sync.run_key_for(SyncTrigger.MANUAL, timezone.now())
    run.save(update_fields=["trigger", "run_key"])
    recovered = no.classify("lovable", since=since, rc=0, manual=True)
    assert recovered.outcome == no.SUCCEEDED
    assert recovered.counts["updated"] == 3
    # The scheduled reading does not take a person's run for the night's.
    assert no.classify("lovable", since=since, rc=0).outcome == no.SKIPPED_UNEXPECTED


def test_a_manual_recovery_is_not_excused_by_the_nights_scheduled_key(since):
    """«Already ran tonight» excuses a scheduled firing, never a recovery that wrote
    no record of its own."""
    _control()
    LovableSyncRun.objects.create(
        run_key=lovable_sync.run_key_for(SyncTrigger.SCHEDULED, timezone.now()),
        trigger=SyncTrigger.SCHEDULED,
        mode=SyncMode.APPLY,
        status=SyncRunStatus.SUCCEEDED,
        started_at=since - dt.timedelta(hours=1),
        mapping_version="test",
    )
    assert no.classify("lovable", since=since, rc=0, manual=True).outcome == (no.SKIPPED_UNEXPECTED)
    assert no.classify("lovable", since=since, rc=3, manual=True).outcome == no.FAILED
