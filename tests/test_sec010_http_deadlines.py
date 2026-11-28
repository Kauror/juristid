# ruff: noqa: F811 - the door fixtures are imported by name and used as parameters
"""SEC-010: every register call has a whole-call deadline, and the night's runs stop on time.

`app.register_watch.sources.http` had a socket timeout on every call and a
whole-call deadline only when a caller passed one (a person's «Lisa märkena»,
a Riigikogu stage). The nightly register watch, the sweep, the ADR forms and the
Government agenda passed none: a server that dripped its answer a byte at a
time, a stalled name lookup or a rate-limit wait could hold a night step for as
long as it liked (docs/adr/0231). These tests hold the fix:

* the run's deadline (`run_deadline`, or the nightly step's
  ``JURISTID_STEP_DEADLINE``) and `CALL_SECONDS` bound a call nobody passed a
  deadline to — name lookup, connect, TLS, headers, body, spacing;
* the Riigikogu client's retries and back-offs and the model requests stop at it;
* a run that reaches it closes as partial, and the cycle's verdict says so.

The «real socket» tests run a TLS server on 127.0.0.1 (the fixtures of
`tests/test_register_file_budget.py`); the others replace the network and the
clock. Nothing leaves the machine.
"""

from __future__ import annotations

import socket
import time
import urllib.request
from types import SimpleNamespace

import pytest

from app.core import nightly_outcome as no
from app.register_watch import ai_call
from app.register_watch.sources import http
from app.register_watch.sources.http import DeadlineExceeded, FetchError
from tests.test_register_file_budget import (  # noqa: F401 - fixtures used by name
    HEAD,
    HOST,
    _drip,
    _Slow,
    net,
    tls_server,
)

SLACK = 0.9


@pytest.fixture(autouse=True)
def no_step_deadline(monkeypatch):
    monkeypatch.delenv(http.STEP_DEADLINE_ENV, raising=False)


def _timed(call) -> tuple[float, BaseException | None]:
    start = time.monotonic()
    try:
        call()
    except BaseException as error:
        return time.monotonic() - start, error
    return time.monotonic() - start, None


# -- a call nobody gave a deadline is still bounded ----------------------------------------


def test_the_nightly_step_deadline_cuts_a_trickling_body(tls_server, monkeypatch):
    """The night's adapters pass no deadline; the step's environment is enough."""
    monkeypatch.setenv(http.STEP_DEADLINE_ENV, f"{time.time() + 1.0:.3f}")
    tls_server(lambda conn: (conn.sendall(HEAD), _drip(conn, b"x" * 60, every=0.1)))

    elapsed, error = _timed(lambda: http.get_bytes(HOST, "/eelnou.pdf"))

    assert isinstance(error, DeadlineExceeded)
    assert elapsed < 1.0 + SLACK


def test_the_run_deadline_cuts_dripping_headers(tls_server):
    tls_server(lambda conn: _drip(conn, HEAD * 20, every=0.1))

    with http.run_deadline(1.0):
        elapsed, error = _timed(lambda: http.get_json(HOST, "/otsing.json"))

    assert isinstance(error, DeadlineExceeded)
    assert elapsed < 1.0 + SLACK


def test_the_run_deadline_cuts_a_server_that_never_answers(tls_server):
    tls_server(lambda conn: time.sleep(4))

    with http.run_deadline(1.0):
        elapsed, error = _timed(lambda: http.get_bytes(HOST, "/vaikiv"))

    assert isinstance(error, DeadlineExceeded)
    assert elapsed < 1.0 + SLACK


def test_the_call_ceiling_bounds_a_call_outside_any_run(tls_server, monkeypatch):
    monkeypatch.setattr(http, "CALL_SECONDS", 1.0)
    tls_server(lambda conn: (conn.sendall(HEAD), _drip(conn, b"x" * 60, every=0.1)))

    elapsed, error = _timed(lambda: http.get_bytes(HOST, "/eelnou.pdf"))

    assert isinstance(error, DeadlineExceeded)
    assert elapsed < 1.0 + SLACK


def test_a_stalled_name_lookup_is_abandoned_at_the_deadline(monkeypatch, settings):
    settings.REGISTER_WATCH_FETCH = True
    monkeypatch.setattr(http, "_last_call", {})
    monkeypatch.setattr(
        http,
        "_OPENER",
        urllib.request.build_opener(http._NoRedirect, http._WatchedHTTPSHandler()),
    )
    real = socket.getaddrinfo

    def slow(host, *args, **kwargs):
        if host == HOST:
            time.sleep(3)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 9))]
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", slow)

    with http.run_deadline(0.5):
        elapsed, error = _timed(lambda: http.get_bytes(HOST, "/x"))

    assert isinstance(error, DeadlineExceeded)
    assert elapsed < 0.5 + SLACK


# -- the waits and the forms ---------------------------------------------------------------


def test_the_host_spacing_is_not_slept_past_the_run_deadline(net):
    http._last_call["adr.rik.ee"] = net.clock.now
    with http.run_deadline(0.5), pytest.raises(DeadlineExceeded):
        http.get_text("adr.rik.ee", "/jm/otsing")
    assert net.clock.slept == [] and net.network.opened == []


def test_a_search_form_keeps_the_deadline_and_the_door(net):
    with pytest.raises(FetchError, match="keelatud"):
        http.post_form("example.com", "/otsing", {"title": "x"})
    with http.run_deadline(-1), pytest.raises(DeadlineExceeded):
        http.post_form("adr.rik.ee", "/jm/otsing", {"title": "x"})
    net.network.routes["https://adr.rik.ee/jm/otsing"] = _Slow(
        net.clock, [b"Kokku: 0 <table></table>"], per_read=0
    )
    assert "Kokku" in http.post_form("adr.rik.ee", "/jm/otsing", {"title": "x"}, deadline=None)
    assert net.network.opened[-1][1] == http.TIMEOUT_SECONDS


def test_limits_only_narrow_and_end_with_their_block():
    assert http.remaining() is None
    with http.run_deadline(100):
        outer = http.remaining()
        with http.run_deadline(1000):
            assert http.remaining() <= outer
        with http.run_deadline(1):
            assert http.remaining() <= 1
        assert http.remaining() > 1
    assert http.remaining() is None


def test_an_unreadable_step_deadline_is_spent_never_unbounded(monkeypatch, net):
    monkeypatch.setenv(http.STEP_DEADLINE_ENV, "kunagi")
    assert http.out_of_time()
    with pytest.raises(DeadlineExceeded):
        http.get_json("adr.envir.ee", "/x.json")
    assert net.network.opened == []


# -- the Riigikogu client's retries and back-offs -------------------------------------------


@pytest.fixture
def riigikogu(monkeypatch, settings):
    from app.riigikogu import client

    settings.REGISTER_WATCH_FETCH = True
    slept: list[float] = []
    calls: list[str] = []
    monkeypatch.setattr(client, "_wait_turn", lambda path: None)
    monkeypatch.setattr(client, "sleep", slept.append)
    return SimpleNamespace(client=client, slept=slept, calls=calls)


def test_a_retry_after_longer_than_the_time_left_ends_the_reading(riigikogu, monkeypatch):
    def answer(host, path, params=None, **kwargs):
        riigikogu.calls.append(path)
        raise FetchError("api.riigikogu.ee vastas 429", status=429, retry_after=100)

    monkeypatch.setattr(http, "get_json", answer)
    with http.run_deadline(50), pytest.raises(DeadlineExceeded):
        riigikogu.client.get_json("/api/volumes")
    assert riigikogu.calls == ["/api/volumes"]
    assert riigikogu.slept == []


def test_no_request_starts_once_the_run_deadline_passed(riigikogu, monkeypatch):
    monkeypatch.setattr(http, "get_json", lambda *a, **k: riigikogu.calls.append("x"))
    with http.run_deadline(-1), pytest.raises(riigikogu.client.OutOfTime) as stopped:
        riigikogu.client.get_json("/api/volumes")
    assert isinstance(stopped.value, DeadlineExceeded)
    assert riigikogu.calls == []


def test_retries_of_a_dropped_connection_are_exhausted_not_endless(riigikogu, monkeypatch):
    def dropped(*args, **kwargs):
        riigikogu.calls.append("x")
        raise FetchError("api.riigikogu.ee ei vastanud (ConnectionResetError)")

    monkeypatch.setattr(http, "get_json", dropped)
    with http.run_deadline(600), pytest.raises(FetchError):
        riigikogu.client.get_json("/api/volumes")
    assert len(riigikogu.calls) == 3


# -- the model requests -----------------------------------------------------------------


class _Model:
    def __init__(self) -> None:
        self.options: list[dict] = []
        self.created = 0
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def with_options(self, **options):
        self.options.append(options)
        return self

    def _create(self, **kwargs):
        self.created += 1
        raise RuntimeError("no network here")


def _ask(model: _Model):
    return ai_call.call_items(
        setting="REGISTER_WATCH_AI_MODEL",
        system="s",
        prompt=lambda: "p",
        schema={},
        max_tokens=10,
        timeout=ai_call.SWEEP_TIMEOUT,
        key=lambda row: row,
        usage=ai_call.Usage(),
        client=model,
    )


def test_no_model_request_starts_once_the_time_is_spent():
    model = _Model()
    with http.run_deadline(-1):
        failure = _ask(model)
    assert isinstance(failure, ai_call.Failure) and failure.reason == ai_call.OUT_OF_TIME
    assert model.created == 0


def test_a_model_request_near_the_deadline_gets_no_more_than_the_time_left():
    model = _Model()
    with http.run_deadline(30):
        _ask(model)
    (options,) = model.options
    assert options["timeout"] <= 30
    assert options["max_retries"] == 0
    assert model.created == 1


# -- the verdicts -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rc", "outcome", "word"),
    [
        (124, no.FAILED, "TIMEOUT"),
        (75, no.SKIPPED_UNEXPECTED, "BUSY"),
        (70, no.INTERRUPTED, "STUCK"),
    ],
)
def test_the_supervisors_exit_code_overrides_a_tidy_record(rc, outcome, word):
    tidy = no.StepOutcome("register-sweep", no.SUCCEEDED_NO_NEW, "", {"read": 9})
    found = no._supervised(tidy, rc)
    assert (found.outcome, found.reason.split(":")[0]) == (outcome, word)
    assert found.counts == {"read": 9}
    assert no._supervised(tidy, 0) is tidy


def test_a_deliberate_skip_stays_deliberate_whatever_the_exit_code():
    off = no.StepOutcome("government-agenda", no.SKIPPED_DELIBERATE, "reading is off")
    assert no._supervised(off, 124) is off


def test_a_run_stopped_at_its_time_limit_is_partial_never_clean():
    run = SimpleNamespace(
        errors=[{"viga": "Kontroll peatus: ajapiir täis", "ajapiir": True}],
        finished_at=object(),
        ai_failures=0,
    )
    found = no._ledger("register-watch", run, 0, read=12, new=0, other_running=False)
    assert found.outcome == no.PARTIAL and "time limit" in found.reason
    nothing = no._ledger("register-watch", run, 0, read=0, new=0, other_running=False)
    assert nothing.outcome == no.FAILED and "time limit" in nothing.reason


# -- the budgets fit the schedule ----------------------------------------------------------


def test_every_budget_and_its_termination_fit_inside_the_steps_allowance():
    from app.core import nightly_schedule as schedule

    for step, budget in schedule.EXECUTION_BUDGET.items():
        assert budget > schedule.GRACE, step
        assert budget + schedule.TERMINATION <= schedule.STEP_ALLOWANCE[step], step
    # The Riigikogu cycle's own limit sits inside its soft deadline.
    from app.riigikogu import cycle

    soft = schedule.EXECUTION_BUDGET["riigikogu"] - schedule.GRACE
    assert cycle.CYCLE_TIME_LIMIT <= soft


def test_the_cycle_script_runs_each_step_with_the_scheduled_budget():
    from django.conf import settings

    from app.core import nightly_schedule as schedule

    text = (settings.BASE_DIR / "scripts" / "deploy" / "juristid-nightly-cycle.sh").read_text(
        encoding="utf-8"
    )
    for step, budget in schedule.EXECUTION_BUDGET.items():
        seconds = int(budget.total_seconds())
        assert f"    {step}) seconds={seconds} ;;" in text, step
        assert f"run --step {step} --seconds {seconds} --" in text, step
    assert f"GRACE={int(schedule.GRACE.total_seconds())}" in text
    assert "python manage.py watch_registers" not in text
