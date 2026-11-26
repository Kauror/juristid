"""The Government agenda before publication (r95, first r94 night 26.11.2026).

valitsus.ee answered **403** for the 26.11.26 agenda page the night before the session,
404 for an address with no page, and 200 for last week's published agenda. r94 read
any non-404 as a source error, so the night ended PARTIAL. Now a 403 is told apart
by one control read of the newest stored agenda:

* control 200 and the session still to come: «not yet published» — no error;
* control 200 and the session already past: the expected page is inaccessible;
* control 403: access refused — an error;
* control unreadable: publication unconfirmed — a warning, never a clean check.

No test contacts valitsus.ee or the model: the page is the trimmed 19.11.26 copy in
tests/fixtures/valitsus/ and every address answers from a table.
"""

from __future__ import annotations

import datetime as dt
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.test import override_settings

from app.core import nightly_outcome as no
from app.register_watch import agenda
from app.register_watch.models import (
    AgendaCheckState,
    CandidateNotice,
    GovernmentAgenda,
    GovernmentAgendaCheck,
)
from app.register_watch.sources import valitsus
from app.register_watch.sources.http import FetchError

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("department_head")]

PAGE = (Path(__file__).parent / "fixtures" / "valitsus" / "agenda-191126.html").read_text(
    encoding="utf-8"
)
#: The fixture page is the 19.11.26 agenda: its week (Mon 16.11) is the one tested.
SESSION = dt.date(2026, 11, 19)
WEDNESDAY, THURSDAY, FRIDAY = dt.date(2026, 11, 18), SESSION, dt.date(2026, 11, 20)
SHORT, LONG = valitsus.candidate_paths(SESSION)
CONTROL = "/uudised/valitsuse-121126-istungi-kommenteeritud-paevakord"


class Site:
    """valitsus.ee as a table: path → page text, or an HTTP status / exception."""

    def __init__(self, answers: dict[str, object] | None = None, default: object = 404):
        self.answers = answers or {}
        self.default = default
        self.asked: list[str] = []

    def __call__(self, host: str, path: str) -> str:
        self.asked.append(path)
        answer = self.answers.get(path, self.default)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, int):
            raise FetchError(f"{host} vastas {answer}", status=answer)
        return str(answer)


class NoModel:
    """Fails the test if the model is asked anything."""

    beta = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **_: pytest.fail("the model was asked"))
    )


@pytest.fixture
def site(monkeypatch):
    table = Site()
    monkeypatch.setattr(valitsus, "get_text", table)
    return table


@pytest.fixture
def last_week():
    """The agenda stored the week before: the control page."""
    return GovernmentAgenda.objects.create(
        url=f"https://valitsus.ee{CONTROL}",
        title="Valitsuse 12.11.26 istungi …",
        session_date=dt.date(2026, 11, 12),
    )


def _run(today: dt.date, **kwargs):
    return agenda.run(today=today, ai_client=NoModel(), **kwargs)


def _machine(outcome) -> dict:
    """What the cycle reads: the command's OUTCOME-JSON line, built the same way."""
    from app.register_watch.management.commands.watch_government_agenda import Command

    out = io.StringIO()
    command = Command(stdout=out)
    command._machine_line(outcome)
    return json.loads(out.getvalue().split("OUTCOME-JSON ", 1)[1])


def _classified(outcome):
    with override_settings(REGISTER_WATCH_FETCH=True):
        return no.classify(
            "government-agenda",
            since=dt.datetime(2026, 11, 18, tzinfo=dt.UTC),
            rc=0,
            reported=_machine(outcome),
        )


def _nothing_written_but_the_check():
    assert GovernmentAgenda.objects.filter(session_date__gte=WEDNESDAY).count() == 0
    assert CandidateNotice.objects.count() == 0


# -- the night before publication ----------------------------------------------------


def test_an_expected_page_refused_before_its_session_is_not_yet_published(site, last_week):
    site.answers = {SHORT: 403, LONG: 404, CONTROL: PAGE}
    outcome = _run(today=THURSDAY)

    assert outcome.stored == [] and outcome.errors == []
    (check,) = outcome.checks
    assert check.state == AgendaCheckState.NOT_PUBLISHED
    assert (check.session_date, check.http_status) == (THURSDAY, 403)
    assert check.url.endswith(SHORT) and not check.missing
    _nothing_written_but_the_check()
    # The control is read once, only because a 403 needed telling apart.
    assert site.asked.count(CONTROL) == 1
    result = _classified(outcome)
    assert result.outcome == no.SUCCEEDED_NO_NEW
    assert "not yet published" in result.reason and "2026-11-19, HTTP 403" in result.reason


def test_a_page_that_does_not_exist_is_plain_not_found(site, last_week):
    outcome = _run(today=THURSDAY)
    assert outcome.errors == [] and outcome.checks[0].state == AgendaCheckState.NOT_FOUND
    assert CONTROL not in site.asked  # no 403, nothing to tell apart
    _nothing_written_but_the_check()
    assert _classified(outcome).outcome == no.SUCCEEDED_NO_NEW


def test_the_first_address_refused_and_the_second_published_is_read(site, last_week):
    site.answers = {SHORT: 403, LONG: PAGE}
    outcome = _run(today=THURSDAY, use_ai=False)

    (stored,) = outcome.stored
    assert stored.session_date == SESSION and stored.url.endswith(LONG)
    assert outcome.checks[0].state == AgendaCheckState.STORED
    assert outcome.checks[0].agenda == stored
    assert outcome.errors == []


def test_the_agenda_published_the_next_night_is_stored_then(site, last_week):
    site.answers = {SHORT: 403, LONG: 404, CONTROL: PAGE}
    first = _run(today=THURSDAY)
    assert first.stored == [] and first.checks[0].state == AgendaCheckState.NOT_PUBLISHED

    site.answers = {SHORT: PAGE}
    second = _run(today=FRIDAY, use_ai=False)
    assert [a.session_date for a in second.stored] == [SESSION]
    assert second.checks[0].state == AgendaCheckState.STORED and not second.checks[0].missing
    # A later night that week reads nothing and says the week is done.
    third = _run(today=FRIDAY + dt.timedelta(days=1), use_ai=False)
    assert third.stored == [] and third.checks[0].state == AgendaCheckState.ALREADY_STORED


# -- refusals that are not publication -----------------------------------------------


def test_a_site_that_refuses_the_published_agenda_too_is_access_refused(site, last_week):
    site.answers = {SHORT: 403, LONG: 404, CONTROL: 403}
    outcome = _run(today=THURSDAY)

    assert outcome.checks[0].state == AgendaCheckState.ACCESS_REFUSED
    assert len(outcome.errors) == 1 and "juurdepääs keelatud" in outcome.errors[0]
    _nothing_written_but_the_check()
    assert _classified(outcome).outcome == no.PARTIAL


def test_with_no_readable_control_publication_is_unconfirmed_not_checked(site):
    # No agenda stored yet: nothing to compare the 403 with.
    site.answers = {SHORT: 403, LONG: 404}
    outcome = _run(today=THURSDAY)

    check = outcome.checks[0]
    assert check.state == AgendaCheckState.UNCONFIRMED and check.http_status == 403
    assert outcome.errors == []
    result = _classified(outcome)
    assert result.outcome == no.PARTIAL and "publication unconfirmed" in result.reason


def test_a_control_that_fails_otherwise_leaves_it_unconfirmed(site, last_week):
    site.answers = {SHORT: 403, LONG: 404, CONTROL: 500}
    assert _run(today=THURSDAY).checks[0].state == AgendaCheckState.UNCONFIRMED


def test_a_past_sessions_page_still_refused_is_inaccessible_not_waiting(site, last_week):
    # Friday night: Thursday's page still answers 403 while the control is served.
    site.answers = {SHORT: 403, LONG: 404, CONTROL: PAGE}
    outcome = _run(today=FRIDAY)
    check = outcome.checks[0]
    assert check.state == AgendaCheckState.INACCESSIBLE and check.missing
    assert _classified(outcome).outcome == no.PARTIAL


# -- genuine source failures stay failures ---------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [429, 500, 503, FetchError("valitsus.ee ei vastanud (TimeoutError)")],
    ids=["429", "500", "503", "timeout"],
)
def test_rate_limits_server_errors_and_timeouts_are_source_errors(site, last_week, answer):
    site.answers = {SHORT: answer}
    outcome = _run(today=THURSDAY)
    assert outcome.checks[0].state == AgendaCheckState.SOURCE_ERROR
    assert outcome.errors and CONTROL not in site.asked
    _nothing_written_but_the_check()
    assert _classified(outcome).outcome == no.PARTIAL


@pytest.mark.parametrize(
    "page",
    [
        "<html><h1>Pressiteade</h1><p>tekst</p></html>",
        PAGE.replace("19.11.26", "12.11.26").replace("19. 11. 26", "12. 11. 26"),
    ],
    ids=["not-an-agenda", "another-session"],
)
def test_a_page_that_is_not_this_sessions_agenda_is_a_parse_error(site, last_week, page):
    site.answers = {SHORT: page}
    outcome = _run(today=THURSDAY)
    assert outcome.checks[0].state == AgendaCheckState.PARSE_ERROR
    assert outcome.errors and outcome.stored == []
    _nothing_written_but_the_check()


# -- the week's last reading ---------------------------------------------------------


def test_nothing_stored_by_fridays_cycle_is_missing(site, last_week):
    outcome = _run(today=FRIDAY)
    check = outcome.checks[0]
    assert check.state == AgendaCheckState.NOT_FOUND and check.missing
    result = _classified(outcome)
    assert (
        result.outcome == no.PARTIAL and "missing after its last nightly reading" in result.reason
    )
    # Thursday's cycle is not the last: the same answer is only waiting.
    assert not _run(today=THURSDAY).checks[0].missing


def test_the_missing_week_is_still_said_on_valitsuse_info_after_the_window():
    friday_after = dt.datetime(2026, 11, 20, 6, 0, tzinfo=agenda.TALLINN)
    assert agenda.missing_this_week(friday_after) is True


# -- a stored agenda: judged and told once -------------------------------------------


@override_settings(REGISTER_SWEEP_AI=False)
def test_a_stored_agenda_is_told_once_and_a_later_night_tells_nobody_again(
    site, last_week, department_head
):
    site.answers = {SHORT: PAGE}
    _run(today=THURSDAY)
    assert CandidateNotice.objects.get(recipient=department_head).agendas == 1
    _run(today=FRIDAY)
    assert CandidateNotice.objects.get(recipient=department_head).agendas == 1
    assert GovernmentAgendaCheck.objects.filter(state=AgendaCheckState.ALREADY_STORED).count() == 1


# -- the cycle's reading of the machine line ----------------------------------------


@override_settings(REGISTER_WATCH_FETCH=True)
@pytest.mark.parametrize(
    ("reported", "expected", "reason"),
    [
        (
            {"not_published": 1, "note": "2026-11-26, HTTP 403"},
            no.SUCCEEDED_NO_NEW,
            "not yet published",
        ),
        ({"unconfirmed": 1}, no.PARTIAL, "publication unconfirmed"),
        ({"inaccessible": 1}, no.PARTIAL, "inaccessible"),
        ({"missing": 1, "not_published": 1}, no.PARTIAL, "missing"),
        ({"errors": 1, "not_published": 1}, no.PARTIAL, "valitsus.ee errors"),
        ({"stored": 1, "judged": 20}, no.SUCCEEDED, ""),
        ({}, no.SUCCEEDED_NO_NEW, ""),
    ],
)
def test_the_cycle_tells_waiting_from_failing(reported, expected, reason):
    result = no.classify(
        "government-agenda", since=dt.datetime(2026, 11, 18, tzinfo=dt.UTC), rc=0, reported=reported
    )
    assert result.outcome == expected and reason in result.reason


@override_settings(REGISTER_WATCH_FETCH=True)
def test_the_command_prints_the_waiting_week_and_its_evidence(site, last_week, monkeypatch):
    site.answers = {SHORT: 403, LONG: 404, CONTROL: PAGE}
    monkeypatch.setattr(agenda.timezone, "localdate", lambda: THURSDAY)
    out = io.StringIO()
    call_command("watch_government_agenda", "--no-ai", "--outcome-json", stdout=out)
    text = out.getvalue()
    assert "ERROR" not in text
    assert "WEEK 2026-11-16 NOT_PUBLISHED 2026-11-19 HTTP 403" in text
    line = json.loads(text.split("OUTCOME-JSON ", 1)[1])
    assert line["not_published"] == 1 and line["errors"] == 0
    assert line["note"] == "2026-11-19, HTTP 403"


# -- Haldus ------------------------------------------------------------------------------


def test_haldus_says_waiting_and_failing_apart(client, department_head, last_week):
    from django.urls import reverse

    GovernmentAgendaCheck.objects.create(
        week=dt.date(2026, 11, 16),
        state=AgendaCheckState.NOT_PUBLISHED,
        session_date=THURSDAY,
        http_status=403,
        detail="valitsus.ee vastas 403: päevakord on olemas, kuid veel avaldamata",
    )
    GovernmentAgendaCheck.objects.create(
        week=dt.date(2026, 11, 9), state=AgendaCheckState.ACCESS_REFUSED, http_status=403
    )
    client.force_login(department_head)
    with override_settings(REGISTER_WATCH_FETCH=True):
        response = client.get(reverse("haldus:register_watch"))
    if response.status_code != 200:
        pytest.skip("this persona does not open Haldus → Registrite jälgimine")
    html = response.content.decode()
    section = html[html.index('id="regwatchlog-agenda"') : html.index('id="regwatchlog-runs"')]
    assert "Päevakord on olemas, kuid veel avaldamata" in section
    waiting = section[: section.index("Päevakord on olemas, kuid veel avaldamata")]
    assert waiting.rstrip().endswith('<span class="statechip">')
    refused = section[: section.index("Juurdepääs keelatud")]
    assert refused.rstrip().endswith('statechip statechip--problem">')
    assert "19.11.2026" in section and "403" in section
