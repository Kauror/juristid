"""Nightly history and per-source freshness (R98-09, R98-10, R98-11, docs/adr/0237)."""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from app.core import integration_status, nightly_outcome
from app.core.models import NightlyStepRecord
from app.register_watch.models import (
    AgendaCheckState,
    GovernmentAgenda,
    GovernmentAgendaCheck,
)
from tests.admin_session import apply_admin_gate, full_admin, technical_admin, verified

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 11, 28, 21, 55, tzinfo=dt.UTC)  # 23:55 Tallinn (winter: UTC+2)


@pytest.fixture(autouse=True)
def _gate(settings):
    return apply_admin_gate(settings)


def _result(step: str, outcome: str, reason: str = "") -> nightly_outcome.StepOutcome:
    return nightly_outcome.StepOutcome(step, outcome, reason, {"checked": 3})


def test_remember_keeps_one_durable_row_per_cycle_and_step():
    nightly_outcome.remember(
        _result("register-watch", nightly_outcome.SUCCEEDED),
        rc=0,
        seconds=120,
        night="n1",
        cycle_start=START,
    )
    nightly_outcome.remember(  # the same night again (a re-record) updates, never duplicates
        _result("register-watch", nightly_outcome.PARTIAL, "TIMEOUT"),
        rc=124,
        seconds=4200,
        night="n1",
        cycle_start=START,
    )
    row = NightlyStepRecord.objects.get()
    assert (row.outcome, row.rc, row.reason, row.counts) == (
        "PARTIAL",
        124,
        "TIMEOUT",
        {"checked": 3},
    )


def test_a_record_failure_never_breaks_the_cycle(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db")

    monkeypatch.setattr(NightlyStepRecord.objects, "update_or_create", boom)
    nightly_outcome.remember(
        _result("lovable", nightly_outcome.SUCCEEDED), rc=0, cycle_start=START
    )  # does not raise


def test_history_marks_a_step_with_no_record_and_never_a_night_without_records():
    nightly_outcome.remember(
        _result("lovable", nightly_outcome.SUCCEEDED), rc=0, seconds=30, cycle_start=START
    )
    after = START + dt.timedelta(days=1)  # every step's finish-by has passed
    nights = integration_status.history(30, now=after)
    assert len(nights) == 1  # nothing is invented for other nights
    by_step = {s.step: s for s in nights[0].steps}
    assert by_step["lovable"].record is not None and not by_step["lovable"].missing
    assert by_step["register-watch"].missing  # a step with no record is «kirje puudub», not green
    early = integration_status.history(30, now=START + dt.timedelta(minutes=10))[0]
    assert not any(s.missing for s in early.steps)  # not yet due


def test_the_command_records_what_the_classifier_said(settings):
    call_command(
        "nightly_outcome",
        "--step=register-watch",
        f"--since={START.isoformat()}",
        "--rc=0",
        "--record",
        "--seconds=5",
        "--night=x",
    )
    row = NightlyStepRecord.objects.get()
    assert row.step == "register-watch" and row.cycle_start == START
    assert row.outcome in nightly_outcome.OUTCOMES


def test_agenda_freshness_separates_attempt_success_observed_and_stored():
    stored = GovernmentAgenda.objects.create(title="a", session_date=dt.date(2026, 11, 20))
    GovernmentAgendaCheck.objects.create(
        week=dt.date(2026, 11, 23),
        state=AgendaCheckState.NOT_PUBLISHED,
        session_date=None,
        discovery_ok=True,
    )
    GovernmentAgendaCheck.objects.create(
        week=dt.date(2026, 11, 23),
        state=AgendaCheckState.STORED,
        session_date=dt.date(2026, 11, 27),
        discovery_ok=True,
    )
    agenda = next(s for s in integration_status.sources() if s.key == "government-agenda")
    assert agenda.observed == "2026-11-27" and agenda.stored == stored.session_date.isoformat()
    # the source showed a newer session than is stored: a recorded discrepancy
    assert agenda.state == integration_status.ATTENTION and "uuemat" in agenda.line
    assert agenda.last_attempt is not None and agenda.last_success is not None


def test_an_expected_publication_delay_is_not_an_alarm():
    GovernmentAgenda.objects.create(title="a", session_date=dt.date(2026, 11, 20))
    GovernmentAgendaCheck.objects.create(
        week=dt.date(2026, 11, 23), state=AgendaCheckState.NOT_PUBLISHED, discovery_ok=True
    )
    agenda = next(s for s in integration_status.sources() if s.key == "government-agenda")
    assert agenda.state == integration_status.EXPECTED


def test_a_source_with_no_success_is_unknown_not_fresh(settings):
    settings.REGISTER_SWEEP = True
    sweep = next(s for s in integration_status.sources() if s.key == "register-sweep")
    assert sweep.state == integration_status.UNKNOWN and sweep.last_success is None


def test_the_pages_are_for_the_technical_administrator_only(client):
    for name in ("haldus:nightly_history", "haldus:freshness"):
        assert client.get(reverse(name)).status_code in (302, 404)
    tech, secret = technical_admin()
    full_admin()
    verified(client, tech, secret)
    NightlyStepRecord.objects.create(
        cycle_start=timezone.now() - dt.timedelta(days=1),
        step="lovable",
        outcome="SUCCEEDED",
        recorded_at=timezone.now(),
    )
    for name in ("haldus:nightly_history", "haldus:freshness"):
        page = client.get(reverse(name))
        assert page.status_code == 200
    assert "Kirje puudub" in client.get(reverse("haldus:nightly_history")).content.decode()


def _request():
    from django.test import RequestFactory

    return RequestFactory().get("/haldus/")


def test_the_overview_says_unknown_before_any_night_is_recorded_and_flags_a_problem_after():
    from app.haldus import overview

    assert overview._nightly(_request()).state == overview.UNKNOWN
    nightly_outcome.remember(
        _result("register-watch", nightly_outcome.FAILED, "rc 1"), rc=1, cycle_start=START
    )
    signal = overview._nightly(_request())
    assert signal.state == overview.PROBLEM  # a failed (and missing) step is never «korras»


def test_the_overview_lines_for_parity_and_processing():
    from app.haldus import overview
    from app.legacy_import.lovable_sync_models import LovableParityReport

    assert overview._parity(_request()).state == overview.UNKNOWN
    LovableParityReport.objects.create(
        at=timezone.now(),
        mode="full",
        teemad=3,
        counts={"MATCH": 30},
        mismatches=[["2099_1", "title"]],
    )
    assert overview._parity(_request()).state == overview.PROBLEM
    assert overview._processing(_request()).state == overview.OK
