"""`verification.writer_for` and `services.may_write`: every check, afresh, at every call.

The `mcp-writer` process asks `writer_for` before each prepare and commit.
Nothing is cached: a revoked grant, ``revoke-write``, a deactivation, a role
change or a security-epoch bump answer None at the very next call. And it
never writes (it runs where the connection may as well be read-only).
"""

from __future__ import annotations

import dataclasses
import uuid

import pytest
from django.db import connection
from django.db.models import F
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.accounts.models import User
from app.core import authorization
from app.mcp_access import services
from app.mcp_access.models import McpAccessGrant, McpToken
from app.mcp_access.verification import (
    NO_FAMILY,
    VerifiedAccess,
    reader_for,
    verify_access_token,
    writer_for,
)
from tests import factories
from tests import mcp_access_support as support

pytestmark = pytest.mark.django_db

BOTH = "juristid.read juristid.write"


@pytest.fixture(autouse=True)
def writes_on(settings):
    support.enable(settings)
    settings.MCP_WRITES_ENABLED = True


@pytest.fixture
def moment(monkeypatch):
    return support.freeze_totp(monkeypatch)


@pytest.fixture
def writer(moment):
    """A specialist with write, connected with read + write: (user, grant, access)."""
    user = factories.UserFactory()
    grant, secret = support.enrolled(user, moment)
    services.grant_write(user=user, provenance=support.OPERATOR)
    issued = support.tokens_for(user, secret, moment, scope=BOTH)
    access = verify_access_token(issued.access_token)
    assert access is not None
    return user, grant, access


def test_a_write_token_for_a_write_grant_names_the_writer(writer):
    user, _, access = writer
    assert writer_for(access) == user
    assert access.scopes == frozenset({"juristid.read", "juristid.write"})


def test_verified_access_carries_the_token_family(writer):
    _, _, access = writer
    assert isinstance(access.family_id, uuid.UUID) and access.family_id != NO_FAMILY
    assert McpToken.objects.get(pk=access.token_id).family_id == access.family_id


def test_a_read_only_token_of_a_write_grant_is_no_writer(moment):
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    services.grant_write(user=user, provenance=support.OPERATOR)
    access = verify_access_token(support.tokens_for(user, secret, moment).access_token)
    assert access is not None and reader_for(access) == user
    assert writer_for(access) is None


def test_a_hand_built_access_with_the_scope_claimed_is_no_writer(writer):
    """The scope is read from the token row, not from the `VerifiedAccess` handed in."""
    user, _, access = writer
    McpToken.objects.filter(pk=access.token_id).update(scopes="juristid.read")
    assert writer_for(access) is None
    assert reader_for(access) == user


def test_an_access_naming_another_family_is_no_writer(writer):
    _, _, access = writer
    assert writer_for(dataclasses.replace(access, family_id=uuid.uuid4())) is None
    assert writer_for(dataclasses.replace(access, family_id=NO_FAMILY)) is None


def test_an_access_naming_another_user_or_grant_is_no_writer(writer):
    _, _, access = writer
    other = factories.UserFactory()
    assert writer_for(dataclasses.replace(access, user_id=other.pk)) is None
    assert writer_for(dataclasses.replace(access, grant_id=uuid.uuid4())) is None


def test_revoke_write_takes_effect_at_the_next_call(writer):
    user, _, access = writer
    assert writer_for(access) == user
    services.revoke_write(user=user, reason="test", provenance=support.OPERATOR)
    assert writer_for(access) is None
    assert reader_for(access) == user


def test_revoke_all_writes_takes_effect_at_the_next_call(writer):
    user, _, access = writer
    services.revoke_all_writes(reason="test", provenance=support.OPERATOR)
    assert writer_for(access) is None
    assert reader_for(access) == user


def test_a_revoked_grant_takes_effect_at_the_next_call(writer):
    user, _, access = writer
    services.revoke_grant(user=user, reason="test", provenance=support.OPERATOR)
    assert writer_for(access) is None and reader_for(access) is None


def test_a_deactivated_account_takes_effect_at_the_next_call(writer):
    user, _, access = writer
    User.objects.filter(pk=user.pk).update(is_active=False)
    assert writer_for(access) is None


def test_a_security_epoch_bump_takes_effect_at_the_next_call(writer):
    user, _, access = writer
    User.objects.filter(pk=user.pk).update(security_epoch=F("security_epoch") + 1)
    assert writer_for(access) is None


@pytest.mark.parametrize("role", ["READER", "ADMINISTRATOR"])
def test_a_role_change_away_from_business_write_takes_effect_at_the_next_call(writer, role):
    user, _, access = writer
    # The epoch is deliberately left alone: the role alone must be enough.
    User.objects.filter(pk=user.pk).update(role=role)
    assert writer_for(access) is None


def test_a_break_glass_administrator_never_writes(writer, monkeypatch):
    user, _, access = writer
    User.objects.filter(pk=user.pk).update(role="ADMINISTRATOR")
    monkeypatch.setattr(authorization, "active_break_glass_grant_id", lambda user: uuid.uuid4())
    assert writer_for(access) is None


def test_writes_switched_off_mean_no_writer(writer, settings):
    _, _, access = writer
    settings.MCP_WRITES_ENABLED = False
    assert writer_for(access) is None


def test_mcp_switched_off_means_no_writer(writer, settings):
    _, _, access = writer
    settings.MCP_ENABLED = False
    assert writer_for(access) is None


def test_writer_for_only_reads(writer):
    user, _, access = writer
    with CaptureQueriesContext(connection) as queries:
        assert writer_for(access) == user
    statements = [query["sql"].lstrip().upper() for query in queries.captured_queries]
    assert statements and all(sql.startswith("SELECT") for sql in statements)


def test_may_write_asks_every_question(moment, settings):
    user = factories.UserFactory()
    grant, _ = support.enrolled(user, moment)
    assert not services.may_write(user, grant)  # no write decision yet
    services.grant_write(user=user, provenance=support.OPERATOR)
    grant.refresh_from_db()
    assert services.may_write(user, grant)
    assert not services.may_write(None, grant)
    assert not services.may_write(user, None)
    assert not services.may_write(factories.UserFactory(), grant)  # somebody else's grant
    settings.MCP_WRITES_ENABLED = False
    assert not services.may_write(user, grant)


def test_may_write_is_false_for_a_reader_even_with_the_row_forced(moment):
    reader = factories.ReaderFactory()
    grant, _ = support.enrolled(reader, moment)
    McpAccessGrant.objects.filter(pk=grant.pk).update(write_enabled_at=grant.created_at)
    grant.refresh_from_db()
    assert grant.write_is_enabled
    assert not services.may_write(reader, grant)


def test_the_fakes_default_family_is_never_a_real_one():
    built = VerifiedAccess(
        token_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        grant_id=uuid.uuid4(),
        client_id="x",
        scopes=frozenset({"juristid.read", "juristid.write"}),
        resource=support.RESOURCE,
        expires_at=timezone.now(),
    )
    assert built.family_id == NO_FAMILY
    assert writer_for(built) is None
