"""The Riigikogu cycle stops inside its time limits (ADR 0224 §5, ADR 0229).

A fake clock: `client.clock` reads it and `client.sleep` advances it, and the
fenced door (`http.get_json`) costs fake seconds per call. No test sleeps or
reaches api.riigikogu.ee.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid as uuid_module
from collections.abc import Callable
from typing import Any

import pytest
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from app.core import nightly_outcome, nightly_schedule
from app.register_watch.sources import http
from app.register_watch.sources.http import FetchError
from app.riigikogu import client, cycle
from app.riigikogu.enums import RunStatus, Stage
from app.riigikogu.models import StageRun

pytestmark = pytest.mark.django_db
#: The real ledger turn, before the fixture replaces it.
REAL_WAIT_TURN = client._wait_turn

READING = (
    Stage.BILLS,
    Stage.INVITATIONS,
    Stage.AGENDAS,
    Stage.PROTOCOLS,
    Stage.STENOGRAMS,
    Stage.RESPONSES,
)


class FakeTime:
    """Seconds since the fake night began; `sleep` is the only way it moves (and calls)."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.slept.append(seconds)
        self.now += seconds


class FakeDoor:
    """The door: each call costs ``cost`` fake seconds, then answers or raises ``answer``."""

    def __init__(self, fake: FakeTime, *, cost: float, answer: Any = None) -> None:
        self.fake = fake
        self.cost = cost
        self.answer = answer if answer is not None else {"ok": True}
        self.calls: list[dict[str, Any]] = []

    def get_json(self, host: str, path: str, params: Any = None, **kwargs: Any) -> Any:
        self.calls.append({"path": path, **kwargs})
        if "deadline" in kwargs:
            # The door's watchdog: a call is cut at its deadline (real seconds left
            # then are the fake seconds the client allowed it).
            allowed = max(kwargs["deadline"] - time.monotonic(), 0.0)
            if self.cost > allowed:
                self.fake.now += allowed
                raise FetchError("api.riigikogu.ee ei vastanud ettenähtud aja jooksul")
        self.fake.now += self.cost
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def fake(monkeypatch) -> FakeTime:
    clock = FakeTime()
    monkeypatch.setattr(client, "clock", clock.clock)
    monkeypatch.setattr(client, "sleep", clock.sleep)
    # The ledger's turn as the per-path limit spaces it (11 a minute: 5.45 s);
    # the real ledger compares database time, which a fake clock does not move.
    monkeypatch.setattr(client, "_wait_turn", lambda path: client._nap(60 / 11))
    return clock


def _door(monkeypatch, fake: FakeTime, **kwargs: Any) -> FakeDoor:
    door = FakeDoor(fake, **kwargs)
    monkeypatch.setattr(http, "get_json", door.get_json)
    return door


def _reader(path: str) -> Callable[[client.Budget], Any]:
    """A stage that reads until something stops it (its budget, its time, an error)."""

    def run(budget: client.Budget) -> Any:
        while True:
            try:
                client.get_json(path, budget=budget)
            except client.BudgetExceeded:
                raise
            except FetchError:
                continue  # as the stages do: one document's error, then the next

    return run


def _one_call(path: str) -> Callable[[client.Budget], Any]:
    def run(budget: client.Budget) -> Any:
        client.get_json(path, budget=budget)
        return None

    return run


def _steps(monkeypatch, runs: dict[str, Callable[[client.Budget], Any]]) -> None:
    today = dt.date(2026, 11, 26)
    monkeypatch.setattr(
        cycle,
        "_steps",
        lambda today_, now: [cycle.Step(stage, (today, today), runs[stage]) for stage in READING],
    )


ON = override_settings(RIIGIKOGU_MONITORING=True, REGISTER_WATCH_FETCH=True)


# -- the limits themselves -------------------------------------------------------------


def test_every_stage_can_spend_its_whole_budget_at_a_normal_pace_inside_its_limit():
    for stage in READING:
        need = (
            cycle.BUDGETS[stage] * cycle.REQUEST_SECONDS + cycle.BACKOFF_ALLOWANCE.total_seconds()
        )
        assert cycle.STAGE_TIME_LIMITS[stage].total_seconds() >= need, stage
    # The normal pace: the per-path limit (5.45 s) and up to 2 s for the answer.
    assert cycle.REQUEST_SECONDS >= 60 / client.PER_PATH_PER_MINUTE + 2
    assert cycle.REQUEST_SECONDS >= http.SPACING_SECONDS[client.HOST]
    total = sum((cycle.STAGE_TIME_LIMITS[s] for s in READING), dt.timedelta())
    assert total == dt.timedelta(minutes=87)
    assert total <= cycle.CYCLE_TIME_LIMIT == dt.timedelta(minutes=90)


def test_the_riigikogu_finish_by_is_the_watch_a_lock_wait_the_limit_and_ten_minutes():
    by = nightly_schedule.FINISH_BY
    assert by["riigikogu"] == (
        by["register-watch"]
        + nightly_schedule.LOCK_WAIT
        + cycle.CYCLE_TIME_LIMIT
        + dt.timedelta(minutes=10)
    )
    # No RUNNING stage of a live cycle is called interrupted.
    assert cycle.RUNNING_AT_MOST > cycle.CYCLE_TIME_LIMIT + dt.timedelta(minutes=10)
    # A stage starts at most watch + lock wait + limit into the cycle; across a
    # 25-hour night that is still inside the 30-hour staleness.
    latest_start = by["register-watch"] + nightly_schedule.LOCK_WAIT + cycle.CYCLE_TIME_LIMIT
    assert dt.timedelta(hours=25) + latest_start < cycle.STALE_AFTER


# -- a stage at its limit ----------------------------------------------------------------


@ON
def test_a_stage_at_its_time_limit_stops_partial_and_the_next_stage_runs(monkeypatch, fake):
    # Every call takes 30 s to answer: 60 requests would need 35 min, BILLS has 10.
    door = _door(monkeypatch, fake, cost=30.0)
    runs = {stage: _one_call(f"/{stage}") for stage in READING}
    runs[Stage.BILLS] = _reader("/api/volumes/drafts")
    _steps(monkeypatch, runs)

    done = {r.stage: r for r in cycle.run(today=dt.date(2026, 11, 26))}

    bills = done[Stage.BILLS]
    assert bills.status == RunStatus.PARTIAL
    assert bills.errors[0]["ajapiir"] == "etapp"
    assert 0 < bills.requests < cycle.BUDGETS[Stage.BILLS]
    assert cycle.checkpoint(Stage.BILLS) is None  # the window is read again tomorrow
    assert done[Stage.INVITATIONS].status == RunStatus.SUCCEEDED
    assert cycle.checkpoint(Stage.INVITATIONS) == dt.date(2026, 11, 26)
    # BILLS took no more than its limit; every call it made had a deadline.
    bills_calls = [c for c in door.calls if c["path"] == "/api/volumes/drafts"]
    assert all("deadline" in c for c in bills_calls)
    # A request counted when its ledger wait found no time left was never made.
    assert bills.requests - len(bills_calls) in (0, 1)
    health = {h.stage: h for h in cycle.health()}
    assert health[Stage.BILLS].state == "partial"
    assert "ajapiirini" in health[Stage.BILLS].line


@ON
def test_the_worst_answers_still_end_the_cycle_inside_its_limit(monkeypatch, fake):
    """Every call answers 429 with the longest Retry-After the client accepts (120 s),
    and every stage reads for ever: without the limits that is
    570 × (5.45 + 120) s ≈ 20 hours. With them, each stage ends by its own limit."""
    _door(
        monkeypatch,
        fake,
        cost=25.0,  # the door's whole socket timeout before the answer
        answer=FetchError("api.riigikogu.ee vastas 429", status=429, retry_after=120.0),
    )
    _steps(monkeypatch, {stage: _reader(f"/api/{stage}") for stage in READING})
    starts: dict[str, float] = {}
    real_run_step = cycle._run_step

    def timed(cycle_id: Any, step: cycle.Step) -> StageRun:
        starts[step.stage] = fake.now
        record = real_run_step(cycle_id, step)
        took = fake.now - starts[step.stage]
        assert took <= cycle.STAGE_TIME_LIMITS[step.stage].total_seconds(), step.stage
        return record

    monkeypatch.setattr(cycle, "_run_step", timed)

    done = {r.stage: r for r in cycle.run(today=dt.date(2026, 11, 26))}

    assert fake.now <= cycle.CYCLE_TIME_LIMIT.total_seconds()
    assert {done[s].status for s in READING} == {RunStatus.PARTIAL}
    assert done[Stage.ANALYSIS].status == RunStatus.DISABLED
    assert all(done[s].rate_limited > 0 for s in READING)
    assert all(cycle.checkpoint(s) is None for s in READING)


@ON
def test_a_stage_with_no_time_left_is_recorded_as_not_run_never_missing(monkeypatch, fake):
    # Give the first two stages more than the cycle has: the cycle's own limit decides.
    limits = dict(cycle.STAGE_TIME_LIMITS)
    limits[Stage.BILLS] = dt.timedelta(minutes=60)
    limits[Stage.INVITATIONS] = dt.timedelta(minutes=60)
    monkeypatch.setattr(cycle, "STAGE_TIME_LIMITS", limits)
    monkeypatch.setattr(cycle, "BUDGETS", {**cycle.BUDGETS, Stage.BILLS: 10_000})
    monkeypatch.setattr(cycle, "BUDGETS", {**cycle.BUDGETS, Stage.INVITATIONS: 10_000})
    door = _door(monkeypatch, fake, cost=1.0)
    _steps(monkeypatch, {stage: _reader(f"/api/{stage}") for stage in READING})
    since = timezone.now()

    done = {r.stage: r for r in cycle.run(today=dt.date(2026, 11, 26))}

    assert fake.now <= cycle.CYCLE_TIME_LIMIT.total_seconds()
    assert done[Stage.BILLS].errors[0]["ajapiir"] == "etapp"
    assert done[Stage.INVITATIONS].errors[0]["ajapiir"] == "etapp"
    for stage in (Stage.AGENDAS, Stage.PROTOCOLS, Stage.STENOGRAMS, Stage.RESPONSES):
        record = done[stage]
        assert record.status == RunStatus.PARTIAL
        assert record.requests == 0
        assert record.errors == [{"viga": cycle.NOT_RUN, "ajapiir": "tsükkel"}]
        assert not [c for c in door.calls if c["path"] == f"/api/{stage}"]
    # The night's step is PARTIAL, not INTERRUPTED: every stage has its row.
    outcome = nightly_outcome.classify("riigikogu", since=since, rc=0)
    assert outcome.outcome == nightly_outcome.PARTIAL
    assert outcome.counts["missing"] == 0
    lines = {h.stage: h.line for h in cycle.health()}
    assert "käivitamata" in lines[Stage.AGENDAS]


# -- the client under a limit ------------------------------------------------------------


def test_a_retry_after_that_would_pass_the_limit_ends_the_reading_without_sleeping(
    monkeypatch, fake
):
    _door(
        monkeypatch,
        fake,
        cost=0.0,
        answer=FetchError("api.riigikogu.ee vastas 429", status=429, retry_after=90.0),
    )
    budget = client.Budget(limit=10)
    with client.time_limit(fake.now + 60), pytest.raises(client.OutOfTime):
        client.get_json("/api/x", budget=budget)
    assert 90.0 not in fake.slept
    assert fake.now < 60
    assert (budget.spent, budget.rate_limited) == (1, 1)


def test_no_request_starts_once_the_limit_has_passed(monkeypatch, fake):
    door = _door(monkeypatch, fake, cost=0.0)
    budget = client.Budget(limit=10)
    with client.time_limit(fake.now - 1), pytest.raises(client.OutOfTime):
        client.get_json("/api/x", budget=budget)
    assert door.calls == [] and budget.spent == 0


def test_the_ledger_wait_never_sleeps_past_the_limit(monkeypatch, fake):
    monkeypatch.setattr(client, "_take_turn", lambda key: 50.0)  # the path's minute is full
    with client.time_limit(fake.now + 20), pytest.raises(client.OutOfTime):
        REAL_WAIT_TURN("/api/events")
    assert fake.slept == []


def test_each_call_gets_the_earlier_of_the_limit_and_its_own_ceiling(monkeypatch, fake):
    door = _door(monkeypatch, fake, cost=0.0)
    with client.time_limit(fake.now + 3600):
        before = time.monotonic()
        client.get_json("/api/far")
    with client.time_limit(fake.now + 10):
        client.get_json("/api/near")
    after = time.monotonic()
    far, near = door.calls
    assert far["deadline"] - before <= client.CALL_SECONDS + 1
    assert far["deadline"] - before >= client.CALL_SECONDS - 1
    assert near["deadline"] - after <= 10
    # Outside a limit (the register watch's own reads) the door is called as before.
    client.get_json("/api/watch")
    assert "deadline" not in door.calls[-1]


@pytest.mark.django_db(transaction=True)
def test_a_ledger_lock_held_too_long_is_a_fetch_error_not_a_hang(monkeypatch):
    monkeypatch.setattr(client, "LEDGER_LOCK_TIMEOUT_MS", 200)
    other = connection.copy()
    try:
        with other.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", [client.LEDGER_LOCK])
        started = time.monotonic()
        with pytest.raises(FetchError, match="hõivatud"):
            REAL_WAIT_TURN("/api/x")
        assert time.monotonic() - started < 10
    finally:
        with other.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [client.LEDGER_LOCK])
        other.close()


# -- health --------------------------------------------------------------------------------


@ON
def test_a_running_stage_is_interrupted_only_after_the_cycle_could_have_ended():
    started = timezone.now() - cycle.CYCLE_TIME_LIMIT
    run = StageRun.objects.create(
        cycle=uuid_module.uuid4(), stage=Stage.BILLS, status=RunStatus.RUNNING, started_at=started
    )
    assert {h.stage: h.state for h in cycle.health()}[Stage.BILLS] != "failed"
    later = run.started_at + cycle.RUNNING_AT_MOST + dt.timedelta(minutes=1)
    assert {h.stage: h.line for h in cycle.health(later)}[Stage.BILLS] == "Kontroll katkes"
