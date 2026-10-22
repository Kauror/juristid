"""The `mcp` process's bearer check refuses all but a live token for a reader (ADR 0185 §5).

`verify_access_token` and `reader_for` are the whole interface the `mcp`
process has to this app (CONTRACT "C → B"). Every way a token can stop being
good is tried here, and each answer is the same `None`. They must also work on
a read-only database connection and never write.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.db.models import F
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.accounts.enums import UserRole
from app.accounts.models import User
from app.mcp_access import credentials, services
from app.mcp_access.models import GrantStatus, McpToken, TokenKind
from app.mcp_access.verification import VerifiedAccess, reader_for, verify_access_token
from tests import factories
from tests import mcp_access_support as support

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def mcp_on(settings):
    support.enable(settings)


@pytest.fixture
def moment(monkeypatch):
    return support.freeze_totp(monkeypatch)


@pytest.fixture
def connected(moment):
    user = factories.UserFactory()
    grant, secret = support.enrolled(user, moment)
    issued = support.tokens_for(user, secret, moment)
    return user, grant, issued


def test_a_live_token_names_its_account_grant_client_scope_and_resource(connected):
    user, grant, issued = connected

    access = verify_access_token(issued.access_token)

    assert isinstance(access, VerifiedAccess)
    assert access.user_id == user.pk
    assert access.grant_id == grant.pk
    assert access.client_id == support.PUBLIC_CLIENT
    assert access.scopes == frozenset({"juristid.read"})
    assert access.resource == support.RESOURCE
    assert access.expires_at > timezone.now()
    assert reader_for(access) == user


@pytest.mark.parametrize("raw", ["", "x", "Bearer abc", "a" * 42, "a" * 44, "ä" * 43, None, 42])
def test_a_malformed_token_is_refused_without_a_lookup(raw, django_assert_num_queries):
    with django_assert_num_queries(0):
        assert verify_access_token(raw) is None  # type: ignore[arg-type]


def test_a_token_another_issuer_minted_is_just_an_unknown_digest(connected):
    assert verify_access_token(credentials.new_opaque()) is None


def test_an_expired_token_is_refused(connected):
    _, _, issued = connected
    later = timezone.now() + timedelta(hours=2)
    assert verify_access_token(issued.access_token, now=later) is None


def test_a_refresh_token_is_not_an_access_token(connected):
    _, _, issued = connected
    assert verify_access_token(issued.refresh_token) is None


def test_a_revoked_token_is_refused_and_so_is_its_verified_access(connected):
    _, _, issued = connected
    access = verify_access_token(issued.access_token)
    McpToken.objects.filter(digest=credentials.digest(issued.access_token)).update(
        revoked_at=timezone.now()
    )
    assert verify_access_token(issued.access_token) is None
    assert reader_for(access) is None


def test_a_token_for_a_resource_no_longer_configured_is_refused(connected, settings):
    _, _, issued = connected
    settings.MCP_RESOURCE_URLS = ["https://other.example.test/mcp"]
    assert verify_access_token(issued.access_token) is None


def test_the_resource_is_compared_without_a_trailing_slash(moment, settings):
    """The tunnel URL is configured with a slash and requested without one."""
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    issued = support.tokens_for(user, secret, moment, resource=support.TUNNEL_RESOURCE)
    access = verify_access_token(issued.access_token)
    assert access is not None and access.resource == support.TUNNEL_RESOURCE


def test_a_token_without_the_required_scope_is_refused(connected, settings):
    _, _, issued = connected
    settings.MCP_SCOPE = "juristid.write"
    assert verify_access_token(issued.access_token) is None


def test_nothing_verifies_while_mcp_access_is_off(connected, settings):
    _, _, issued = connected
    access = verify_access_token(issued.access_token)
    settings.MCP_ENABLED = False
    assert verify_access_token(issued.access_token) is None
    assert reader_for(access) is None


def test_a_switched_off_account_is_refused(connected):
    user, _, issued = connected
    access = verify_access_token(issued.access_token)
    User.objects.filter(pk=user.pk).update(is_active=False)
    assert verify_access_token(issued.access_token) is None
    assert reader_for(access) is None


def test_an_account_moved_to_administrator_without_a_business_role_is_refused(connected):
    user, _, issued = connected
    access = verify_access_token(issued.access_token)
    User.objects.filter(pk=user.pk).update(role=UserRole.ADMINISTRATOR)
    assert verify_access_token(issued.access_token) is None
    assert reader_for(access) is None


def test_a_security_epoch_bump_ends_every_token(connected):
    user, _, issued = connected
    access = verify_access_token(issued.access_token)
    User.objects.filter(pk=user.pk).update(security_epoch=F("security_epoch") + 1)
    assert verify_access_token(issued.access_token) is None
    assert reader_for(access) is None


def test_a_revoked_grant_ends_every_token(connected):
    user, _, issued = connected
    access = verify_access_token(issued.access_token)
    services.revoke_grant(user=user, reason="testi lõpp", provenance=support.OPERATOR)
    assert verify_access_token(issued.access_token) is None
    assert reader_for(access) is None


def test_a_grant_that_is_not_active_ends_every_token(connected):
    _, grant, issued = connected
    type(grant).objects.filter(pk=grant.pk).update(status=GrantStatus.PENDING_ENROLMENT)
    assert verify_access_token(issued.access_token) is None


def test_reader_for_refuses_a_forged_access_naming_another_account(connected):
    _, _, issued = connected
    access = verify_access_token(issued.access_token)
    other = factories.UserFactory()
    forged = VerifiedAccess(**{**access.__dict__, "user_id": other.pk})
    assert reader_for(forged) is None


def test_verification_only_reads_and_works_in_a_read_only_transaction(connected):
    """The `mcp` process's connections are `default_transaction_read_only=on`."""
    _, _, issued = connected
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        with CaptureQueriesContext(connection) as queries:
            access = verify_access_token(issued.access_token)
            user = reader_for(access)
        # The transaction really is read-only: a write in it is refused.
        with pytest.raises(Exception, match="read-only transaction"), transaction.atomic():
            McpToken.objects.update(last_used_at=timezone.now())
    assert access is not None and user is not None
    statements = [
        query["sql"].lstrip().split(" ", 1)[0].upper() for query in queries.captured_queries
    ]
    assert set(statements) <= {"SELECT", "SET", "SAVEPOINT", "RELEASE"}, statements
    token = McpToken.objects.get(digest=credentials.digest(issued.access_token))
    assert token.last_used_at is None


def test_only_digests_are_stored(connected):
    _, _, issued = connected
    stored = set(McpToken.objects.values_list("digest", flat=True))
    assert issued.access_token not in stored and issued.refresh_token not in stored
    assert credentials.digest(issued.access_token) in stored
    assert McpToken.objects.filter(kind=TokenKind.ACCESS).count() == 1
