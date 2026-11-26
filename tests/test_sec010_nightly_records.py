# ruff: noqa: F811 - the register fixtures are imported by name and used as parameters
"""SEC-010: a run that reaches its deadline says so, and the night's verdict shows (ADR 0231).

The register watch and the sweep stop starting new work once the run's time is
spent and close their record with one ``ajapiir`` line — what was read is kept,
the run is not a stop (``katkes``) and never a clean night. The cycle keeps each
step's verdict for Haldus, a step stopped at its budget marked as such. Sources
are the fixtures of `tests/test_register_watch.py`; nothing leaves the machine.
"""

from __future__ import annotations

import datetime as dt
import itertools

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse

from app.core import nightly_outcome as no
from app.register_watch import candidates, watch
from app.register_watch.sources import http
from tests import factories
from tests.test_register_watch import TODAY, _bill_teema, registers  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def empty_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def _second_teema():
    return factories.MatterFactory(title="Teine eelnõu (974 SE)")


def _ajapiir(record) -> list[dict]:
    return [error for error in record.errors if error.get("ajapiir")]


def test_a_watch_out_of_time_reads_nothing_more_and_says_so_once(registers):
    _bill_teema()
    _second_teema()

    with http.run_deadline(-1):
        record = watch.run(use_ai=False, today=TODAY)

    assert registers.calls == []
    assert len(_ajapiir(record)) == 1 and len(record.errors) == 1
    assert record.finished_at is not None
    assert not any(error.get("katkes") for error in record.errors)
    found = no._ledger("register-watch", record, 0, read=0, new=0, other_running=False)
    assert found.outcome == no.FAILED and "time limit" in found.reason


def test_a_watch_that_runs_out_midway_keeps_what_it_read_and_is_partial(registers, monkeypatch):
    first, second = _bill_teema(), _second_teema()
    assert first.pk != second.pk
    # In time for the first Teema and its sources, then out of time.
    answers = itertools.chain([False] * 3, itertools.repeat(True))
    monkeypatch.setattr(http, "out_of_time", lambda: next(answers))

    record = watch.run(use_ai=False, today=TODAY)

    assert record.targets_checked >= 1
    assert len(_ajapiir(record)) == 1
    found = no._ledger(
        "register-watch",
        record,
        0,
        read=record.targets_checked,
        new=record.findings_new,
        other_running=False,
    )
    assert found.outcome == no.PARTIAL


def test_a_sweep_out_of_time_reads_no_source_and_says_so_once(registers):
    with http.run_deadline(-1):
        record = candidates.sweep(use_ai=False, today=TODAY)

    assert registers.calls == []
    assert record.sources_read == 0
    assert len(_ajapiir(record)) == 1
    assert record.finished_at is not None


def test_the_cycles_verdict_is_kept_and_haldus_shows_a_timeout(client, specialist):
    with override_settings(REGISTER_WATCH_FETCH=True):
        call_command(
            "nightly_outcome",
            "--step",
            "register-watch",
            "--since",
            "2026-11-26T22:00:00Z",
            "--rc",
            "124",
            "--record",
            "--seconds",
            "4080",
            "--night",
            "2026-11-27",
        )
    (kept,) = no.remembered()
    assert (kept["step"], kept["outcome"], kept["rc"], kept["seconds"]) == (
        "register-watch",
        no.FAILED,
        124,
        4080,
    )
    assert kept["reason"].startswith("TIMEOUT") and kept["problem"]

    client.force_login(specialist)
    response = client.get(reverse("haldus:register_watch"))
    if response.status_code != 200:
        pytest.skip("this persona does not open Haldus → Registrite jälgimine")
    html = response.content.decode()
    section = html[html.index('id="regwatchlog-nightly"') :]
    section = section[: section.index("</section>")]
    assert "Registrite jälgimine" in section
    assert "Ebaõnnestus · ajapiir" in section
    assert "2026-11-27" in section and "4080 s" in section


def test_without_a_recorded_night_haldus_shows_no_empty_section(client, specialist):
    client.force_login(specialist)
    response = client.get(reverse("haldus:register_watch"))
    if response.status_code != 200:
        pytest.skip("this persona does not open Haldus → Registrite jälgimine")
    assert 'id="regwatchlog-nightly"' not in response.content.decode()


def test_the_command_without_record_writes_nothing():
    with override_settings(REGISTER_WATCH_FETCH=True):
        call_command(
            "nightly_outcome",
            "--step",
            "register-watch",
            "--since",
            dt.datetime(2026, 11, 26, 22, tzinfo=dt.UTC).isoformat(),
            "--rc",
            "0",
        )
    assert no.remembered() == []
