"""Wrong authenticator codes cannot be guessed at forever (MCPSEC-01, docs/adr/0185 §3).

The personal authenticator is the only thing that makes an MCP connection
individual: the gate password is shared, and the address typed on the consent
page is no secret. A colleague who knows the gate password could otherwise
script five guesses every fifteen minutes against somebody else's grant for a
year. So:

* consecutive locks escalate — 15 minutes, 1 hour, 4 hours — and the next one
  suspends the grant until an operator runs ``mcp_access unlock``;
* a cumulative wrong-code counter is reset only by a successful authorization;
* every lock and every suspension writes an `McpAccessEvent` and a WARNING
  log line, neither carrying a code;
* one gate session, and one client address, can only fail so many times in a
  window, whichever accounts it names — an address nobody has included;
* the consent page does not say whether an address names anybody, or whether
  that account has a grant, is locked or is suspended, until the account's own
  current code has been verified.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.utils import timezone

from app.mcp_access import services
from app.mcp_access.models import (
    McpAccessEvent,
    McpAccessEventType,
    McpAccessGrant,
    McpAuthorizationCode,
)
from tests import factories
from tests import mcp_access_support as support
from tests.gate import PASSWORD, apply_shared_gate

pytestmark = pytest.mark.django_db

AUTHORIZE = "/oauth/authorize"
GENERIC = "Kood ei sobi või sellel kasutajal pole MCP ligipääsu"


@pytest.fixture(autouse=True)
def behind_the_gate(settings):
    apply_shared_gate(settings)
    support.enable(settings)


@pytest.fixture
def moment(monkeypatch):
    return support.freeze_totp(monkeypatch)


def _url(**overrides) -> str:
    return f"{AUTHORIZE}?{urlencode(support.authorize_params(**overrides))}"


def _through_the_gate(client: Client) -> None:
    assert client.post("/konto/varav/", {"password": PASSWORD}).status_code == 302


def _as(user, code: str) -> dict[str, str]:
    return support.consent(user, code)


def _wrong(user, times: int) -> list[services.AuthorizationOutcome]:
    return [
        services.authorize(
            user=user, request=support.request_for(), totp_code="000000", ip_address=None
        )
        for _ in range(times)
    ]


def _expire_lock(grant: McpAccessGrant) -> None:
    McpAccessGrant.objects.filter(pk=grant.pk).update(
        locked_until=timezone.now() - timedelta(seconds=1)
    )


def _events(event_type: str) -> list[McpAccessEvent]:
    return list(McpAccessEvent.objects.filter(event_type=event_type).order_by("created_at"))


# -- escalation --------------------------------------------------------------------


def test_consecutive_locks_escalate_and_the_fourth_suspends_the_grant(moment):
    user = factories.UserFactory()
    grant, secret = support.enrolled(user, moment)
    expected = [15 * 60, 60 * 60, 4 * 60 * 60]

    for rung, seconds in enumerate(expected, start=1):
        started = timezone.now()
        outcomes = _wrong(user, services.MAX_FAILED_CODES)
        # The fifth wrong code is answered like the other four: whether the
        # grant exists, let alone whether it is locked, is not said to a guess.
        assert {outcome.status for outcome in outcomes} == {"bad_code"}
        grant.refresh_from_db()
        assert grant.lock_count == rung
        assert grant.suspended_at is None
        assert grant.locked_until is not None
        held = (grant.locked_until - started).total_seconds()
        assert seconds - 5 <= held <= seconds + 5, (rung, held)
        _expire_lock(grant)

    outcomes = _wrong(user, services.MAX_FAILED_CODES)
    assert {outcome.status for outcome in outcomes} == {"bad_code"}
    grant.refresh_from_db()
    assert grant.suspended_at is not None
    assert grant.total_failed_attempts == 4 * services.MAX_FAILED_CODES

    # Suspended: no time passing opens it again, the right code included.
    right = services.authorize(
        user=user,
        request=support.request_for(),
        totp_code=support.code_at(secret, moment),
        ip_address=None,
    )
    assert right.status == "suspended"
    assert not McpAuthorizationCode.objects.exists()
    assert len(_events(McpAccessEventType.GRANT_LOCKED)) == 3
    suspended = _events(McpAccessEventType.GRANT_SUSPENDED)
    assert len(suspended) == 1 and suspended[0].grant_id == grant.pk

    assert services.unlock(user=user, provenance=support.OPERATOR) is True
    grant.refresh_from_db()
    assert grant.suspended_at is None and grant.lock_count == 0 and grant.locked_until is None
    # The operator's unlock does not wipe the history; only a success does.
    assert grant.total_failed_attempts == 4 * services.MAX_FAILED_CODES
    assert support.code_for(user, secret, moment, steps=1)
    grant.refresh_from_db()
    assert grant.total_failed_attempts == 0 and grant.failed_attempts == 0


def test_the_cumulative_counter_is_reset_only_by_a_successful_authorization(moment):
    user = factories.UserFactory()
    grant, secret = support.enrolled(user, moment)
    _wrong(user, 7)
    grant.refresh_from_db()
    assert grant.total_failed_attempts == 7
    assert grant.failed_attempts == 2 and grant.lock_count == 1
    _expire_lock(grant)
    _wrong(user, 1)
    grant.refresh_from_db()
    assert grant.total_failed_attempts == 8

    support.code_for(user, secret, moment)

    grant.refresh_from_db()
    assert (grant.total_failed_attempts, grant.failed_attempts, grant.lock_count) == (0, 0, 0)
    assert grant.locked_until is None


def test_wrong_codes_typed_during_a_lock_still_count_towards_the_next_one(moment):
    user = factories.UserFactory()
    grant, _ = support.enrolled(user, moment)
    _wrong(user, services.MAX_FAILED_CODES)
    grant.refresh_from_db()
    first_lock = grant.locked_until
    _wrong(user, services.MAX_FAILED_CODES)
    grant.refresh_from_db()
    assert grant.lock_count == 2
    assert grant.locked_until is not None and first_lock is not None
    assert grant.locked_until > first_lock + timedelta(minutes=30)


def test_the_right_code_during_a_lock_learns_of_the_lock_and_mints_nothing(moment):
    user = factories.UserFactory()
    grant, secret = support.enrolled(user, moment)
    _wrong(user, services.MAX_FAILED_CODES)
    code = support.code_at(secret, moment)

    outcome = services.authorize(
        user=user, request=support.request_for(), totp_code=code, ip_address=None
    )

    assert outcome.status == "locked" and outcome.wait > 0
    assert not McpAuthorizationCode.objects.exists()
    # Spent, so the same code cannot be replayed the moment the lock ends.
    _expire_lock(grant)
    again = services.authorize(
        user=user, request=support.request_for(), totp_code=code, ip_address=None
    )
    assert again.status == "bad_code"


def test_every_lock_and_suspension_is_logged_as_a_warning_without_the_code(moment, caplog):
    user = factories.UserFactory()
    grant, _ = support.enrolled(user, moment)
    caplog.set_level(logging.WARNING, logger="app.mcp_access")
    for _ in range(4):
        _wrong(user, services.MAX_FAILED_CODES)
        _expire_lock(grant)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    messages = [r.getMessage() for r in warnings if r.name.startswith("app.mcp_access")]
    assert len([m for m in messages if "locked" in m]) == 3
    assert len([m for m in messages if "suspended" in m]) == 1
    assert all(str(grant.pk) in m for m in messages)
    assert not any("000000" in m for m in messages)
    for event in _events(McpAccessEventType.GRANT_LOCKED) + _events(
        McpAccessEventType.GRANT_SUSPENDED
    ):
        assert "000000" not in str(event.detail)
        assert event.detail["total_failed_attempts"] > 0


# -- the page says nothing about a grant before the code is verified ---------------


def test_the_page_offers_the_same_fields_to_everybody(client):
    _through_the_gate(client)
    body = client.get(_url()).content.decode()
    assert 'name="epost"' in body
    assert 'name="kood"' in body
    assert "ei ole MCP ligipääsu" not in body


def test_no_grant_and_a_wrong_code_get_the_same_answer(client, moment):
    granted = factories.UserFactory()
    support.enrolled(granted, moment)
    pending = factories.UserFactory()
    services.grant_access(user=pending, provenance=support.OPERATOR)
    nobody = factories.UserFactory()
    _through_the_gate(client)

    answers = []
    for address in (granted.upn, pending.upn, nobody.upn, "keegi.ei.ole@example.invalid"):
        response = client.post(_url(), _as(address, "000000"))
        answers.append(
            (
                response.status_code,
                GENERIC in response.content.decode(),
                'name="kood"' in response.content.decode(),
            )
        )
    assert answers == [(400, True, True)] * 4
    assert not McpAuthorizationCode.objects.exists()


def test_a_locked_grant_looks_like_any_other_until_its_own_code_is_typed(client, moment):
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    _through_the_gate(client)
    for _ in range(services.MAX_FAILED_CODES):
        response = client.post(_url(), _as(user, "000000"))
        assert response.status_code == 400 and GENERIC in response.content.decode()

    page = client.get(_url()).content.decode()
    assert "Liiga palju valesid koode" not in page and 'name="kood"' in page
    wrong = client.post(_url(), _as(user, "111111"))
    assert wrong.status_code == 400 and GENERIC in wrong.content.decode()
    assert "Liiga palju valesid koode" not in wrong.content.decode()

    right = client.post(_url(), _as(user, support.code_at(secret, moment)))
    assert right.status_code == 429
    assert "Liiga palju valesid koode" in right.content.decode()


def test_a_suspended_grant_says_so_only_to_its_own_code(client, moment):
    user = factories.UserFactory()
    grant, secret = support.enrolled(user, moment)
    McpAccessGrant.objects.filter(pk=grant.pk).update(suspended_at=timezone.now())
    _through_the_gate(client)
    wrong = client.post(_url(), _as(user, "000000"))
    assert wrong.status_code == 400 and GENERIC in wrong.content.decode()
    right = client.post(_url(), _as(user, support.code_at(secret, moment)))
    assert right.status_code == 403
    assert "haldaja" in right.content.decode()
    assert not McpAuthorizationCode.objects.exists()


# -- one session or one address cannot spray codes across accounts -----------------


def test_one_gate_session_cannot_spray_codes_across_accounts(client, moment, settings):
    settings.MCP_AUTHORIZE_MAX_FAILURES_PER_SESSION = 4
    settings.MCP_AUTHORIZE_MAX_FAILURES_PER_ADDRESS = 100
    colleagues = [factories.UserFactory() for _ in range(3)]
    for colleague in colleagues:
        support.enrolled(colleague, moment)
    target = factories.UserFactory()
    _, target_secret = support.enrolled(target, moment)
    _through_the_gate(client)
    tries = 0
    for colleague in colleagues:
        for _ in range(2):
            response = client.post(_url(), _as(colleague, "000000"))
            tries += 1
            if tries <= 4:
                assert response.status_code == 400, tries
            else:
                assert response.status_code == 429, tries

    # Even the target's right code is not checked from this session now.
    refused = client.post(_url(), _as(target, support.code_at(target_secret, moment)))
    assert refused.status_code == 429
    assert "Liiga palju katseid" in refused.content.decode()
    assert not McpAuthorizationCode.objects.exists()
    # ... and no account's own counter was charged for the refused attempts.
    assert sum(McpAccessGrant.objects.values_list("total_failed_attempts", flat=True)) == 4

    # Another session from another address is not affected.
    other = Client(REMOTE_ADDR="192.0.2.77")
    _through_the_gate(other)
    assert (
        other.post(_url(), _as(target, support.code_at(target_secret, moment))).status_code == 302
    )


def test_one_client_address_cannot_spray_codes_from_fresh_sessions(moment, settings):
    settings.MCP_AUTHORIZE_MAX_FAILURES_PER_SESSION = 100
    settings.MCP_AUTHORIZE_MAX_FAILURES_PER_ADDRESS = 3
    victim = factories.UserFactory()
    _, secret = support.enrolled(victim, moment)
    statuses = []
    for _ in range(4):
        session = Client(REMOTE_ADDR="198.51.100.9")
        _through_the_gate(session)
        statuses.append(session.post(_url(), _as(victim, "000000")).status_code)
    assert statuses == [400, 400, 400, 429]
    fresh = Client(REMOTE_ADDR="198.51.100.9")
    _through_the_gate(fresh)
    assert fresh.post(_url(), _as(victim, support.code_at(secret, moment))).status_code == 429
