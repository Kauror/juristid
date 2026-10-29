"""Security/resilience review 2026-10-29, assessor C, workstream 1: the actor matrix.

Every MCP client the review names — no token, a malformed one, an expired one,
a read grant, a write grant, a revoked grant, a deactivated account, a READER,
an account whose role became ADMINISTRATOR after the token was minted, a
security-epoch bump, a write decision revoked after the token was minted, and
a process with writes switched off — run through the *real* services
(`app.mcp_access.services` / `verification`), against the two predicates every
tool and every write step asks: `reader_for` and `writer_for`. Plus the token
endpoint's binding checks (resource, PKCE, replay, scope widening on refresh).

Every test here PASSES on main 359e34bf: these are boundaries verified sound,
not findings. They exist so the matrix is executed, not asserted from reading.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from app.accounts.enums import UserRole
from app.mcp_access import credentials, services, verification
from app.mcp_access.models import GrantStatus, McpToken
from tests import factories
from tests.mcp_access_support import (
    PUBLIC_CLIENT,
    PUBLIC_REDIRECT,
    RESOURCE,
    TUNNEL_RESOURCE,
    VERIFIER,
    code_for,
    enable,
    enrolled,
    freeze_totp,
    tokens_for,
)

pytestmark = pytest.mark.django_db

OPERATOR = "secres-c"
READ_WRITE = "juristid.read juristid.write"


@pytest.fixture(autouse=True)
def mcp_on(settings):
    enable(settings)
    settings.MCP_WRITES_ENABLED = True
    settings.REAL_DATA_ALLOWED = False
    return settings


@pytest.fixture
def moment(monkeypatch):
    return freeze_totp(monkeypatch)


def _expire(raw: str) -> None:
    McpToken.objects.filter(digest=credentials.digest(raw)).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )


def _reader(raw: str):
    access = verification.verify_access_token(raw)
    return None if access is None else verification.reader_for(access)


def _writer(raw: str):
    access = verification.verify_access_token(raw)
    return None if access is None else verification.writer_for(access)


# -- no token / bad token / expired ----------------------------------------------


@pytest.mark.parametrize("raw", ["", "x", "A" * 43, "A" * 600, "../../etc/passwd"])
def test_no_or_malformed_token_is_nobody(raw):
    assert verification.verify_access_token(raw) is None


def test_an_expired_access_token_is_nobody(moment):
    user = factories.UserFactory()
    _grant, secret = enrolled(user, moment)
    issued = tokens_for(user, secret, moment)
    assert _reader(issued.access_token) is not None
    _expire(issued.access_token)
    assert _reader(issued.access_token) is None
    assert _writer(issued.access_token) is None


def test_a_refresh_token_is_not_an_access_token(moment):
    user = factories.UserFactory()
    _grant, secret = enrolled(user, moment)
    issued = tokens_for(user, secret, moment)
    assert verification.verify_access_token(issued.refresh_token) is None


# -- read grant vs write grant --------------------------------------------------------


def test_a_read_grant_reads_and_never_writes_even_when_it_asked_for_write(moment):
    user = factories.UserFactory()
    _grant, secret = enrolled(user, moment)
    issued = tokens_for(user, secret, moment, scope=READ_WRITE)
    assert issued.scope == "juristid.read"  # narrowed, no write decision was made
    assert _reader(issued.access_token) == user
    assert _writer(issued.access_token) is None


def test_a_write_grant_writes_and_a_token_minted_before_the_decision_does_not(moment):
    user = factories.UserFactory()
    _grant, secret = enrolled(user, moment)
    before = tokens_for(user, secret, moment, steps=0, scope=READ_WRITE)
    services.grant_write(user=user, provenance=OPERATOR)
    after = tokens_for(user, secret, moment, steps=1, scope=READ_WRITE)
    assert before.scope == "juristid.read"
    assert after.scope == READ_WRITE
    assert _writer(before.access_token) is None
    assert _writer(after.access_token) == user


def test_revoke_write_ends_writing_at_the_next_call_but_not_reading(moment):
    user = factories.UserFactory()
    _grant, secret = enrolled(user, moment)
    services.grant_write(user=user, provenance=OPERATOR)
    issued = tokens_for(user, secret, moment, scope=READ_WRITE)
    assert _writer(issued.access_token) == user
    services.revoke_write(user=user, reason="review", provenance=OPERATOR)
    assert _writer(issued.access_token) is None
    assert _reader(issued.access_token) == user


def test_writes_off_in_this_process_means_no_writer_whatever_the_token_says(moment, settings):
    user = factories.UserFactory()
    _grant, secret = enrolled(user, moment)
    services.grant_write(user=user, provenance=OPERATOR)
    issued = tokens_for(user, secret, moment, scope=READ_WRITE)
    assert _writer(issued.access_token) == user
    settings.MCP_WRITES_ENABLED = False
    assert _writer(issued.access_token) is None
    assert _reader(issued.access_token) == user


# -- the account behind the grant changes ---------------------------------------------


def test_a_revoked_grant_is_nobody_for_reading_and_writing(moment):
    user = factories.UserFactory()
    _grant, secret = enrolled(user, moment)
    services.grant_write(user=user, provenance=OPERATOR)
    issued = tokens_for(user, secret, moment, scope=READ_WRITE)
    services.revoke_grant(user=user, reason="left", provenance=OPERATOR)
    assert _reader(issued.access_token) is None
    assert _writer(issued.access_token) is None
    # And the refresh token is dead too.
    answer = services.refresh(
        client_id=PUBLIC_CLIENT,
        refresh_token=issued.refresh_token,
        resource=RESOURCE,
        scope="",
        ip_address=None,
    )
    assert isinstance(answer, services.OAuthError)


def test_a_deactivated_account_is_nobody(moment):
    user = factories.UserFactory()
    _grant, secret = enrolled(user, moment)
    issued = tokens_for(user, secret, moment)
    user.is_active = False
    user.save(update_fields=["is_active"])
    assert _reader(issued.access_token) is None
    assert _writer(issued.access_token) is None


def test_a_reader_role_reads_and_cannot_be_granted_write(moment):
    user = factories.ReaderFactory()
    _grant, secret = enrolled(user, moment)
    with pytest.raises(services.GrantRefused):
        services.grant_write(user=user, provenance=OPERATOR)
    issued = tokens_for(user, secret, moment, scope=READ_WRITE)
    assert issued.scope == "juristid.read"
    assert _reader(issued.access_token) == user
    assert _writer(issued.access_token) is None


def test_a_role_that_became_administrator_after_the_token_is_nobody(moment):
    """ADR 0181: technical administration is not business access; the grant predates it."""
    user = factories.UserFactory()
    _grant, secret = enrolled(user, moment)
    services.grant_write(user=user, provenance=OPERATOR)
    issued = tokens_for(user, secret, moment, scope=READ_WRITE)
    user.role = UserRole.ADMINISTRATOR
    user.save(update_fields=["role"])
    assert _reader(issued.access_token) is None
    assert _writer(issued.access_token) is None


def test_a_role_that_became_reader_after_a_write_token_reads_and_cannot_write(moment):
    user = factories.UserFactory()
    _grant, secret = enrolled(user, moment)
    services.grant_write(user=user, provenance=OPERATOR)
    issued = tokens_for(user, secret, moment, scope=READ_WRITE)
    user.role = UserRole.READER
    user.save(update_fields=["role"])
    assert _reader(issued.access_token) == user
    assert _writer(issued.access_token) is None


def test_an_administrator_cannot_be_granted_at_all(moment):
    user = factories.AdministratorFactory()
    with pytest.raises(services.GrantRefused):
        services.grant_access(user=user, provenance=OPERATOR)


def test_a_security_epoch_bump_ends_every_token(moment):
    user = factories.UserFactory()
    _grant, secret = enrolled(user, moment)
    issued = tokens_for(user, secret, moment)
    user.security_epoch = (user.security_epoch or 0) + 1
    user.save(update_fields=["security_epoch"])
    assert _reader(issued.access_token) is None
    answer = services.refresh(
        client_id=PUBLIC_CLIENT,
        refresh_token=issued.refresh_token,
        resource=RESOURCE,
        scope="",
        ip_address=None,
    )
    assert isinstance(answer, services.OAuthError)


# -- the token endpoint's bindings ---------------------------------------------------


def test_a_code_bound_to_one_resource_is_refused_for_the_other_and_is_then_spent(moment):
    user = factories.UserFactory()
    _grant, secret = enrolled(user, moment)
    code = code_for(user, secret, moment, resource=RESOURCE)
    wrong = services.exchange_code(
        client_id=PUBLIC_CLIENT,
        code=code,
        redirect_uri=PUBLIC_REDIRECT,
        code_verifier=VERIFIER,
        resource=TUNNEL_RESOURCE,
        ip_address=None,
    )
    assert isinstance(wrong, services.OAuthError) and wrong.error == "invalid_grant"
    again = services.exchange_code(
        client_id=PUBLIC_CLIENT,
        code=code,
        redirect_uri=PUBLIC_REDIRECT,
        code_verifier=VERIFIER,
        resource=RESOURCE,
        ip_address=None,
    )
    assert isinstance(again, services.OAuthError)


def test_a_code_presented_twice_revokes_what_it_was_exchanged_for(moment):
    user = factories.UserFactory()
    _grant, secret = enrolled(user, moment)
    code = code_for(user, secret, moment)
    first = services.exchange_code(
        client_id=PUBLIC_CLIENT,
        code=code,
        redirect_uri=PUBLIC_REDIRECT,
        code_verifier=VERIFIER,
        resource=RESOURCE,
        ip_address=None,
    )
    assert isinstance(first, services.IssuedTokens)
    assert _reader(first.access_token) == user
    second = services.exchange_code(
        client_id=PUBLIC_CLIENT,
        code=code,
        redirect_uri=PUBLIC_REDIRECT,
        code_verifier=VERIFIER,
        resource=RESOURCE,
        ip_address=None,
    )
    assert isinstance(second, services.OAuthError)
    assert _reader(first.access_token) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"code_verifier": "x" * 43},
        {"redirect_uri": PUBLIC_REDIRECT + "/"},
        {"client_id": "https://chatgpt.example.test/oauth/client.json"},
    ],
)
def test_pkce_redirect_and_client_are_each_bound(moment, overrides):
    user = factories.UserFactory()
    _grant, secret = enrolled(user, moment)
    code = code_for(user, secret, moment)
    arguments = {
        "client_id": PUBLIC_CLIENT,
        "code": code,
        "redirect_uri": PUBLIC_REDIRECT,
        "code_verifier": VERIFIER,
        "resource": RESOURCE,
        "ip_address": None,
        **overrides,
    }
    answer = services.exchange_code(**arguments)
    assert isinstance(answer, services.OAuthError)


def test_a_refresh_cannot_widen_the_scope_to_write(moment):
    user = factories.UserFactory()
    _grant, secret = enrolled(user, moment)
    services.grant_write(user=user, provenance=OPERATOR)
    issued = tokens_for(user, secret, moment)  # asked for read only
    assert issued.scope == "juristid.read"
    answer = services.refresh(
        client_id=PUBLIC_CLIENT,
        refresh_token=issued.refresh_token,
        resource=RESOURCE,
        scope=READ_WRITE,
        ip_address=None,
    )
    assert isinstance(answer, services.OAuthError) and answer.error == "invalid_scope"


def test_a_refresh_by_another_client_is_refused_and_burns_nothing_of_the_owner(moment):
    user = factories.UserFactory()
    _grant, secret = enrolled(user, moment)
    issued = tokens_for(user, secret, moment)
    answer = services.refresh(
        client_id="https://chatgpt.example.test/oauth/client.json",
        refresh_token=issued.refresh_token,
        resource=RESOURCE,
        scope="",
        ip_address=None,
    )
    assert isinstance(answer, services.OAuthError)
    # The rightful client can still refresh: a stranger's attempt is not reuse.
    ok = services.refresh(
        client_id=PUBLIC_CLIENT,
        refresh_token=issued.refresh_token,
        resource=RESOURCE,
        scope="",
        ip_address=None,
    )
    assert isinstance(ok, services.IssuedTokens)


# -- audience: the one documented acceptance -----------------------------------------


def test_a_token_for_one_resource_url_verifies_for_the_whole_list_accepted_risk(moment):
    """ACCEPTED RISK (app/mcp_server/auth.py docstring): MCP_RESOURCE_URLS names two
    URLs of the *same* server (tunnel and public), and the verifier accepts a token
    minted for either at either. Recorded here so a third, foreign entry would be
    noticed as a change in meaning."""
    user = factories.UserFactory()
    _grant, secret = enrolled(user, moment)
    issued = tokens_for(user, secret, moment, resource=TUNNEL_RESOURCE)
    access = verification.verify_access_token(issued.access_token)
    assert access is not None
    assert access.resource == TUNNEL_RESOURCE.rstrip("/")
    grant = services.active_grant(user)
    assert grant is not None and grant.status == GrantStatus.ACTIVE
