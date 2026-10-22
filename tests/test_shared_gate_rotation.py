"""Rotating the shared password ends every session it opened (csf_19450e2c, ADR 0183).

The gate's session used to record only *when* the password was typed. Changing
`JURISTID_SHARED_GATE_PASSWORD` therefore changed who could open the door from
now on, and nothing about who was already through it: every session opened
under the old password went on working until it aged out, up to twelve hours
later — persona, HTMX writes, downloads and all.

Now a session also records *which* password it was opened under, as a keyed
digest (`shared_gate.credential_version`), and the gate middleware compares it
on every request. These tests rotate the setting the way an operator rotates
the environment variable and ask every kind of route what it now answers.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta

import pytest
from django.contrib.auth.hashers import check_password
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from app.accounts import shared_gate
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from tests import factories
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

NEW_PASSWORD = "uus-osakonna-parool-pärast-vahetust"  # noqa: S105
HX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture(autouse=True)
def gate_mode(settings):
    return apply_shared_gate(settings)


@pytest.fixture
def gate_url():
    return reverse("accounts:shared_gate")


@pytest.fixture
def marko():
    return factories.UserFactory(display_name="Marko Näidisjurist")


@pytest.fixture
def behind_the_gate(client, gate_url):
    response = client.post(gate_url, {"password": PASSWORD})
    assert response.status_code == 302
    return client


@pytest.fixture
def as_marko(behind_the_gate, marko):
    response = behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(marko.pk)})
    assert response.status_code == 302
    assert behind_the_gate.session["_auth_user_id"] == str(marko.pk)
    return behind_the_gate


def _rotate(settings) -> None:
    settings.SHARED_GATE_PASSWORD = NEW_PASSWORD


def _assert_sent_to_the_gate(response, gate_url) -> None:
    assert response.status_code == 302, response.status_code
    assert response["Location"] == gate_url


# -- every kind of route ---------------------------------------------------------


def test_a_page_asks_for_the_password_again_after_rotation(behind_the_gate, settings, gate_url):
    assert behind_the_gate.get("/osakond/").status_code == 200

    _rotate(settings)

    _assert_sent_to_the_gate(behind_the_gate.get("/osakond/"), gate_url)


def test_a_persona_session_ends_after_rotation(as_marko, settings, gate_url):
    assert as_marko.get(reverse("matters:my_work")).status_code == 200

    _rotate(settings)

    response = as_marko.get(reverse("matters:my_work"))
    _assert_sent_to_the_gate(response, gate_url)
    assert not response.wsgi_request.user.is_authenticated
    # The whole session went: no persona and no gate state survive to be carried
    # into a session re-opened under the new password.
    session = as_marko.session
    assert "_auth_user_id" not in session
    assert shared_gate.GATE_PASSED_AT not in session
    assert shared_gate.GATE_CREDENTIAL not in session


def test_an_htmx_write_is_answered_with_a_sign_in_401_after_rotation(as_marko, settings, gate_url):
    matter = factories.MatterFactory()
    page = reverse("matters:matter_detail", kwargs={"pk": matter.pk})

    _rotate(settings)

    response = as_marko.post(
        reverse("matters:add_note", kwargs={"pk": matter.pk}),
        {"marge-title": "pärast vahetust"},
        **HX,
        HTTP_HX_CURRENT_URL=f"http://testserver{page}",
    )
    assert response.status_code == 401
    assert response["X-Juristid-Failure"] == "sign-in"
    assert response["X-Juristid-Sign-In"] == gate_url


def test_a_download_is_refused_after_rotation(as_marko, settings, gate_url):
    route = reverse("documents:download", kwargs={"pk": uuid.uuid4()})
    # Before: the request reaches the view, which knows no such version.
    assert as_marko.get(route).status_code == 404

    _rotate(settings)

    _assert_sent_to_the_gate(as_marko.get(route), gate_url)


def test_switching_persona_after_rotation_does_not_reopen_the_gate(
    behind_the_gate, marko, settings, gate_url
):
    _rotate(settings)

    response = behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(marko.pk)})

    _assert_sent_to_the_gate(response, gate_url)
    assert "_auth_user_id" not in behind_the_gate.session
    assert not SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.PERSONA_SELECTED, actor=marko
    ).exists()


def test_the_end_is_recorded_once_with_its_reason(as_marko, settings):
    _rotate(settings)

    as_marko.get("/osakond/")
    as_marko.get("/osakond/")

    ended = SecurityAuditEvent.objects.filter(event_type=SecurityEventType.SESSION_ENDED)
    assert ended.count() == 1
    detail = ended.get().detail
    assert detail["reason"] == "shared_password_changed"
    assert detail["path"] == "shared_gate"


# -- and the way back in -----------------------------------------------------------


def test_the_new_password_opens_the_gate_and_the_old_one_does_not(
    behind_the_gate, settings, gate_url
):
    _rotate(settings)
    behind_the_gate.get("/osakond/")

    refused = behind_the_gate.post(gate_url, {"password": PASSWORD})
    assert refused.status_code == 400

    passed = behind_the_gate.post(gate_url, {"password": NEW_PASSWORD})
    assert passed.status_code == 302
    assert behind_the_gate.get("/osakond/").status_code == 200
    assert behind_the_gate.session[shared_gate.GATE_CREDENTIAL] == (
        shared_gate.credential_version()
    )


def test_rotating_back_does_not_revive_a_session_the_rotation_ended(
    behind_the_gate, settings, gate_url
):
    _rotate(settings)
    behind_the_gate.get("/osakond/")

    settings.SHARED_GATE_PASSWORD = PASSWORD

    _assert_sent_to_the_gate(behind_the_gate.get("/osakond/"), gate_url)


# -- persona changes carry the version, never refresh it ---------------------------


def test_a_persona_change_keeps_the_version_it_was_opened_under(behind_the_gate, marko):
    before = behind_the_gate.session[shared_gate.GATE_CREDENTIAL]

    behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(marko.pk)})
    assert behind_the_gate.session[shared_gate.GATE_CREDENTIAL] == before

    behind_the_gate.post(reverse("accounts:act_as"), {"user_id": ""})
    assert behind_the_gate.session[shared_gate.GATE_CREDENTIAL] == before
    assert behind_the_gate.get("/osakond/").status_code == 200


def _request_with_session(client):
    request = RequestFactory().get("/")
    request.session = client.session
    return request


def test_a_carried_pass_is_never_upgraded_to_the_current_version(behind_the_gate, settings):
    """`open_gate(carried=…)` keeps what it is handed — a stale version stays stale."""
    request = _request_with_session(behind_the_gate)
    carried = shared_gate.current_pass(request)
    assert shared_gate.has_passed(request)

    _rotate(settings)
    shared_gate.open_gate(request, carried=carried)

    assert request.session[shared_gate.GATE_CREDENTIAL] == carried.credential
    assert not shared_gate.has_passed(request)


def test_a_carried_pass_without_a_version_does_not_pass(behind_the_gate):
    request = _request_with_session(behind_the_gate)
    carried = shared_gate.current_pass(request)

    shared_gate.open_gate(
        request, carried=shared_gate.GatePass(passed_at=carried.passed_at, credential=None)
    )

    assert shared_gate.GATE_CREDENTIAL not in request.session
    assert not shared_gate.has_passed(request)


def test_a_session_from_before_versions_were_recorded_is_ended(behind_the_gate, gate_url):
    """What every open session looks like on the release that adds the version."""
    session = behind_the_gate.session
    session.pop(shared_gate.GATE_CREDENTIAL)
    session.save()

    _assert_sent_to_the_gate(behind_the_gate.get("/osakond/"), gate_url)
    assert shared_gate.GATE_PASSED_AT not in behind_the_gate.session


# -- the limits are unchanged ------------------------------------------------------


def test_the_absolute_limit_still_applies_with_a_current_version(behind_the_gate, gate_url):
    session = behind_the_gate.session
    session[shared_gate.GATE_PASSED_AT] = (timezone.now() - timedelta(hours=13)).isoformat()
    session.save()

    _assert_sent_to_the_gate(behind_the_gate.get("/osakond/"), gate_url)


def test_the_idle_limit_still_applies_with_a_current_version(behind_the_gate, gate_url):
    session = behind_the_gate.session
    now = timezone.now()
    session[shared_gate.GATE_PASSED_AT] = (now - timedelta(hours=9)).isoformat()
    session[shared_gate.GATE_LAST_SEEN] = (now - timedelta(hours=8, minutes=1)).isoformat()
    session.save()

    _assert_sent_to_the_gate(behind_the_gate.get("/osakond/"), gate_url)


# -- what the session holds --------------------------------------------------------


def test_the_session_holds_no_secret_material(as_marko):
    """Not the password, not its hash, not an unkeyed digest of it."""
    session = as_marko.session
    stored = {key: session[key] for key in session.keys()}
    dumped = json.dumps(stored, default=str)

    assert PASSWORD not in dumped
    assert hashlib.sha256(PASSWORD.encode()).hexdigest()[:32] not in dumped
    assert hashlib.sha256(PASSWORD.encode()).hexdigest() not in dumped
    assert "pbkdf2_sha256$" not in dumped
    for value in stored.values():
        assert not (isinstance(value, str) and check_password(PASSWORD, value))

    version = stored[shared_gate.GATE_CREDENTIAL]
    assert len(version) == 32
    assert int(version, 16) >= 0


def test_the_version_follows_the_configuration_not_the_process(settings):
    """Derived, so every worker computes the same answer with nothing shared."""
    first = shared_gate.credential_version()
    assert first == shared_gate.credential_version()

    settings.SHARED_GATE_PASSWORD = NEW_PASSWORD
    assert shared_gate.credential_version() not in ("", first)

    settings.SHARED_GATE_PASSWORD = PASSWORD
    assert shared_gate.credential_version() == first


def test_an_unconfigured_gate_has_no_version_and_passes_nobody(behind_the_gate, settings):
    settings.SHARED_GATE_PASSWORD = ""
    request = _request_with_session(behind_the_gate)

    assert shared_gate.credential_version() == ""
    assert not shared_gate.has_passed(request)
