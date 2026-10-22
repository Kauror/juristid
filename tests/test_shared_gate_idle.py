"""The shared gate's idle limit: 8 hours without a request (JSEC-005, owner D11).

The 12-hour absolute limit (`SHARED_GATE_SESSION_SECONDS`) stays. On top of it,
a session that has made no request for `SHARED_GATE_IDLE_SECONDS` is asked for
the password again. The last-activity time is written at most every
`shared_gate.LAST_SEEN_RESOLUTION`, so an ordinary page view does not save the
session every time.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.accounts import shared_gate
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

HOUR = timedelta(hours=1)
MINUTE = timedelta(minutes=1)


@pytest.fixture(autouse=True)
def gate_mode(settings):
    apply_shared_gate(settings)
    settings.SHARED_GATE_SESSION_SECONDS = 12 * 60 * 60
    settings.SHARED_GATE_IDLE_SECONDS = 8 * 60 * 60
    return settings


@pytest.fixture
def gate_url():
    return reverse("accounts:shared_gate")


@pytest.fixture
def behind_the_gate(client, gate_url):
    response = client.post(gate_url, {"password": PASSWORD})
    assert response.status_code == 302
    return client


@pytest.fixture
def with_a_persona(behind_the_gate):
    """Past the gate *and* acting as somebody.

    Since docs/adr/0181 `/osakond/` is `@login_required`: a gate session with no
    persona is sent to the persona chooser before the page renders, so a test
    that needs the page's 200 chooses a persona first.
    """
    person = factories.UserFactory(display_name="Kati Näidisjurist")
    behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(person.pk)})
    return behind_the_gate


def _set_clock(client, *, passed_ago: timedelta, seen_ago: timedelta | None) -> None:
    now = timezone.now()
    session = client.session
    session[shared_gate.GATE_PASSED_AT] = (now - passed_ago).isoformat()
    if seen_ago is None:
        session.pop(shared_gate.GATE_LAST_SEEN, None)
    else:
        session[shared_gate.GATE_LAST_SEEN] = (now - seen_ago).isoformat()
    session.save()


def _last_seen(client) -> datetime:
    return datetime.fromisoformat(client.session[shared_gate.GATE_LAST_SEEN])


def test_the_idle_limit_defaults_to_eight_hours():
    from config import settings as project_settings

    assert project_settings.SHARED_GATE_IDLE_SECONDS == 8 * 60 * 60
    assert project_settings.SHARED_GATE_SESSION_SECONDS == 12 * 60 * 60


def test_opening_the_gate_starts_the_idle_clock(behind_the_gate):
    assert timezone.now() - _last_seen(behind_the_gate) < MINUTE


def test_idle_seven_hours_fifty_nine_minutes_is_still_passed(with_a_persona):
    _set_clock(with_a_persona, passed_ago=9 * HOUR, seen_ago=8 * HOUR - MINUTE)
    response = with_a_persona.get("/osakond/")
    assert response.status_code == 200


def test_idle_eight_hours_one_minute_asks_for_the_password_again(behind_the_gate, gate_url):
    _set_clock(behind_the_gate, passed_ago=9 * HOUR, seen_ago=8 * HOUR + MINUTE)
    response = behind_the_gate.get("/osakond/")
    assert response.status_code == 302
    assert response["Location"] == gate_url
    # And the gate page itself asks, rather than waving the session through.
    assert behind_the_gate.get(gate_url).status_code == 200


def test_an_idle_session_loses_its_persona_too(behind_the_gate, gate_url):
    marko = factories.UserFactory(display_name="Marko Näidisjurist")
    behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(marko.pk)})
    _set_clock(behind_the_gate, passed_ago=9 * HOUR, seen_ago=8 * HOUR + MINUTE)

    response = behind_the_gate.get("/minu-asjad/")
    assert response.status_code == 302
    assert response["Location"] == gate_url
    assert not response.wsgi_request.user.is_authenticated


def test_the_twelve_hour_absolute_limit_is_still_enforced(behind_the_gate, gate_url):
    """Active a minute ago, but the password was typed over twelve hours ago."""
    _set_clock(behind_the_gate, passed_ago=12 * HOUR + MINUTE, seen_ago=MINUTE)
    response = behind_the_gate.get("/osakond/")
    assert response.status_code == 302
    assert response["Location"] == gate_url


def test_activity_refreshes_the_idle_clock(with_a_persona):
    _set_clock(with_a_persona, passed_ago=9 * HOUR, seen_ago=7 * HOUR)
    assert with_a_persona.get("/osakond/").status_code == 200
    assert timezone.now() - _last_seen(with_a_persona) < MINUTE

    # Seven more hours of silence from *that* request: within the limit, where
    # fourteen hours from the old stamp would not have been.
    session = with_a_persona.session
    session[shared_gate.GATE_LAST_SEEN] = (timezone.now() - 7 * HOUR).isoformat()
    session.save()
    assert with_a_persona.get("/osakond/").status_code == 200


def test_the_clock_is_written_at_most_every_few_minutes(with_a_persona):
    _set_clock(with_a_persona, passed_ago=HOUR, seen_ago=MINUTE)
    stamp = with_a_persona.session[shared_gate.GATE_LAST_SEEN]

    assert with_a_persona.get("/osakond/").status_code == 200
    assert with_a_persona.session[shared_gate.GATE_LAST_SEEN] == stamp
    assert shared_gate.LAST_SEEN_RESOLUTION <= timedelta(minutes=5)


def test_a_session_from_before_the_idle_clock_is_measured_from_the_password(
    behind_the_gate, gate_url
):
    """A gate opened before this release has no last-seen stamp: count from the pass."""
    _set_clock(behind_the_gate, passed_ago=8 * HOUR + MINUTE, seen_ago=None)
    assert behind_the_gate.get("/osakond/").status_code == 302


def test_a_malformed_last_seen_is_not_a_pass(behind_the_gate, gate_url):
    session = behind_the_gate.session
    session[shared_gate.GATE_LAST_SEEN] = "eile"
    session.save()
    response = behind_the_gate.get("/osakond/")
    assert response.status_code == 302
    assert response["Location"] == gate_url


def test_changing_persona_does_not_restart_the_absolute_clock(behind_the_gate):
    """Picking a name is not typing the password; the 12 hours keep counting."""
    _set_clock(behind_the_gate, passed_ago=11 * HOUR, seen_ago=MINUTE)
    passed_at = behind_the_gate.session[shared_gate.GATE_PASSED_AT]

    marko = factories.UserFactory(display_name="Marko Näidisjurist")
    behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(marko.pk)})
    assert behind_the_gate.session[shared_gate.GATE_PASSED_AT] == passed_at

    behind_the_gate.post(reverse("accounts:act_as"), {"user_id": ""})
    assert behind_the_gate.session[shared_gate.GATE_PASSED_AT] == passed_at


def test_the_idle_limit_is_a_setting(behind_the_gate, settings, gate_url):
    settings.SHARED_GATE_IDLE_SECONDS = 60 * 60
    _set_clock(behind_the_gate, passed_ago=2 * HOUR, seen_ago=HOUR + MINUTE)
    assert behind_the_gate.get("/osakond/").status_code == 302


# -- the setting's own bounds ------------------------------------------------
#
# The idle clock is written at most every `LAST_SEEN_RESOLUTION` (five minutes),
# so a shorter limit would ask an active reader for the password between two
# stamps; and a limit longer than the absolute one can never fire, which reads
# as a decision while deciding nothing. `juristid.E021` refuses both.

IDLE_BOUNDS = "juristid.E021"


def _check_ids() -> set[str]:
    from app.core.checks import check_runtime_safety

    return {problem.id for problem in check_runtime_safety(None)}


@pytest.mark.parametrize("seconds", [0, 60, 299])
def test_an_idle_limit_under_five_minutes_refuses(settings, seconds):
    settings.SHARED_GATE_IDLE_SECONDS = seconds
    assert IDLE_BOUNDS in _check_ids()


def test_an_idle_limit_beyond_the_absolute_limit_refuses(settings):
    settings.SHARED_GATE_IDLE_SECONDS = settings.SHARED_GATE_SESSION_SECONDS + 1
    assert IDLE_BOUNDS in _check_ids()


@pytest.mark.parametrize("seconds", [300, 8 * 60 * 60, 12 * 60 * 60])
def test_an_idle_limit_within_the_bounds_passes(settings, seconds):
    settings.SHARED_GATE_IDLE_SECONDS = seconds
    assert IDLE_BOUNDS not in _check_ids()


def test_the_floor_is_the_stamp_resolution():
    from app.core.checks import MINIMUM_SHARED_GATE_IDLE_SECONDS

    assert MINIMUM_SHARED_GATE_IDLE_SECONDS == 300
    assert shared_gate.LAST_SEEN_RESOLUTION.total_seconds() == MINIMUM_SHARED_GATE_IDLE_SECONDS


def test_the_idle_bounds_belong_to_the_gate_alone(settings):
    settings.AUTH_MODE = "none"
    settings.SHARED_GATE_IDLE_SECONDS = 10
    assert IDLE_BOUNDS not in _check_ids()
