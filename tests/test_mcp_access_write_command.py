"""`manage.py mcp_access grant-write, revoke-write, revoke-all-writes, list` (OAUTH-SCOPES.md)."""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from app.mcp_access import services
from app.mcp_access.models import GrantStatus, McpAccessEvent, McpAccessEventType, McpAccessGrant
from app.mcp_access.verification import reader_for, verify_access_token, writer_for
from tests import factories
from tests import mcp_access_support as support

pytestmark = pytest.mark.django_db

BOTH = "juristid.read juristid.write"


@pytest.fixture(autouse=True)
def mcp_on(settings):
    support.enable(settings)
    settings.MCP_WRITES_ENABLED = True


@pytest.fixture
def moment(monkeypatch):
    return support.freeze_totp(monkeypatch)


def run(*args: str) -> str:
    out = io.StringIO()
    call_command("mcp_access", *args, stdout=out)
    return out.getvalue()


def test_grant_write_enables_write_on_the_open_grant_and_records_who(moment):
    user = factories.UserFactory()
    support.enrolled(user, moment)

    output = run("grant-write", "--user", user.upn)

    grant = McpAccessGrant.objects.get(user=user)
    assert grant.write_is_enabled and grant.write_enabled_at is not None
    assert "manage.py mcp_access grant-write" in grant.write_enabled_via
    assert "Muudatuste ettevalmistamine lubatud" in output
    event = McpAccessEvent.objects.get(event_type=McpAccessEventType.WRITE_ENABLED)
    assert event.grant == grant and "os-user=" in event.provenance
    assert services.may_write(user, grant)


def test_a_department_head_may_be_given_write(moment):
    head = factories.DepartmentHeadFactory()
    support.enrolled(head, moment)
    run("grant-write", "--user", head.upn)
    assert McpAccessGrant.objects.get(user=head).write_is_enabled


def test_grant_write_on_a_pending_grant_is_allowed_but_writes_only_once_active():
    user = factories.UserFactory()
    services.grant_access(user=user, provenance=support.OPERATOR)
    run("grant-write", "--user", user.upn)
    grant = McpAccessGrant.objects.get(user=user)
    assert grant.status == GrantStatus.PENDING_ENROLMENT and grant.write_is_enabled
    assert not services.may_write(user, grant)


@pytest.mark.parametrize(
    ("make", "words"),
    [
        (factories.ReaderFactory, "sisu muutmise rolli"),
        (factories.AdministratorFactory, "sisu muutmise rolli"),
    ],
    ids=["reader", "administrator"],
)
def test_grant_write_refuses_an_account_without_a_business_write_role(make, words, moment):
    user = make()
    if make is factories.ReaderFactory:
        support.enrolled(user, moment)
    with pytest.raises(CommandError, match=words):
        run("grant-write", "--user", user.upn)
    assert not McpAccessGrant.objects.filter(write_enabled_at__isnull=False).exists()
    refused = McpAccessEvent.objects.get(event_type=McpAccessEventType.WRITE_REFUSED)
    assert refused.succeeded is False and words in refused.detail["reason"]


def test_grant_write_refuses_an_inactive_account(moment):
    user = factories.UserFactory()
    support.enrolled(user, moment)
    user.is_active = False
    user.save(update_fields=["is_active"])
    with pytest.raises(CommandError, match="ei ole aktiivne"):
        run("grant-write", "--user", user.upn)
    assert not McpAccessGrant.objects.get(user=user).write_is_enabled


def test_grant_write_refuses_an_account_without_an_open_grant(moment):
    user = factories.UserFactory()
    with pytest.raises(CommandError, match="kehtivat ligipääsu"):
        run("grant-write", "--user", user.upn)
    support.enrolled(user, moment)
    services.revoke_grant(user=user, reason="test", provenance=support.OPERATOR)
    with pytest.raises(CommandError, match="kehtivat ligipääsu"):
        run("grant-write", "--user", user.upn)


def test_grant_write_refuses_a_synthetic_account_where_real_data_is_allowed(moment, settings):
    user = factories.UserFactory(is_synthetic=True)
    support.enrolled(user, moment)
    settings.REAL_DATA_ALLOWED = True
    with pytest.raises(CommandError, match="sünteetilisele"):
        run("grant-write", "--user", user.upn)


def test_grant_write_twice_is_refused_and_changes_nothing(moment):
    user = factories.UserFactory()
    support.enrolled(user, moment)
    run("grant-write", "--user", user.upn)
    before = McpAccessGrant.objects.get(user=user).write_enabled_at
    with pytest.raises(CommandError, match="juba lubatud"):
        run("grant-write", "--user", user.upn)
    assert McpAccessGrant.objects.get(user=user).write_enabled_at == before


def test_grant_write_warns_when_writes_are_switched_off(moment, settings):
    settings.MCP_WRITES_ENABLED = False
    user = factories.UserFactory()
    support.enrolled(user, moment)
    output = run("grant-write", "--user", user.upn)
    assert "MCP_WRITES_ENABLED on välja lülitatud" in output


def test_grant_write_needs_mcp_enabled_but_revoking_write_does_not(moment, settings):
    user = factories.UserFactory()
    support.enrolled(user, moment)
    run("grant-write", "--user", user.upn)
    settings.MCP_ENABLED = False
    with pytest.raises(CommandError, match="MCP_ENABLED is off"):
        run("grant-write", "--user", user.upn)
    run("revoke-write", "--user", user.upn, "--reason", "kontroll")
    run("revoke-all-writes", "--reason", "kontroll")
    assert not McpAccessGrant.objects.get(user=user).write_is_enabled


def test_revoke_write_ends_write_at_once_and_keeps_reading(moment):
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    run("grant-write", "--user", user.upn)
    issued = support.tokens_for(user, secret, moment, scope=BOTH)
    access = verify_access_token(issued.access_token)
    assert access is not None and writer_for(access) == user

    output = run("revoke-write", "--user", user.upn, "--reason", "rollivahetus")

    assert "Lugemine jääb" in output
    assert writer_for(access) is None
    assert reader_for(access) == user
    grant = McpAccessGrant.objects.get(user=user)
    assert grant.status == GrantStatus.ACTIVE and grant.write_revoke_reason == "rollivahetus"
    event = McpAccessEvent.objects.get(event_type=McpAccessEventType.WRITE_REVOKED)
    assert event.detail["reason"] == "rollivahetus"


def test_revoke_write_needs_a_reason_and_a_write_permission(moment):
    user = factories.UserFactory()
    support.enrolled(user, moment)
    with pytest.raises(CommandError, match="põhjus on kohustuslik"):
        run("revoke-write", "--user", user.upn, "--reason", "  ")
    with pytest.raises(CommandError, match="muudatuste ettevalmistamise luba"):
        run("revoke-write", "--user", user.upn, "--reason", "x")


def test_grant_write_after_revoke_write_enables_it_again(moment):
    user = factories.UserFactory()
    support.enrolled(user, moment)
    run("grant-write", "--user", user.upn)
    run("revoke-write", "--user", user.upn, "--reason", "x")
    run("grant-write", "--user", user.upn)
    grant = McpAccessGrant.objects.get(user=user)
    assert grant.write_is_enabled and grant.write_revoked_at is None
    assert McpAccessEvent.objects.filter(event_type=McpAccessEventType.WRITE_ENABLED).count() == 2


def test_revoke_all_writes_ends_every_write_and_leaves_reading(moment):
    first = factories.UserFactory()
    second = factories.DepartmentHeadFactory()
    read_only = factories.UserFactory()
    first_secret = support.enrolled(first, moment)[1]
    second_secret = support.enrolled(second, moment)[1]
    support.enrolled(read_only, moment)
    run("grant-write", "--user", first.upn)
    run("grant-write", "--user", second.upn)
    accesses = [
        verify_access_token(support.tokens_for(person, secret, moment, scope=BOTH).access_token)
        for person, secret in ((first, first_secret), (second, second_secret))
    ]
    assert all(access is not None and writer_for(access) for access in accesses)

    output = run("revoke-all-writes", "--reason", "intsident")

    assert "tühistatud: 2" in output
    for access in accesses:
        assert access is not None
        assert writer_for(access) is None and reader_for(access) is not None
    assert not McpAccessGrant.objects.filter(
        write_enabled_at__isnull=False, write_revoked_at__isnull=True
    ).exists()
    assert set(McpAccessGrant.objects.values_list("status", flat=True)) == {GrantStatus.ACTIVE}
    revoked = McpAccessEvent.objects.filter(event_type=McpAccessEventType.WRITE_REVOKED)
    assert revoked.count() == 2 and all(event.detail["revoke_all_writes"] for event in revoked)


def test_revoke_all_writes_needs_a_reason():
    with pytest.raises(CommandError, match="põhjus on kohustuslik"):
        run("revoke-all-writes", "--reason", " ")


def test_list_shows_the_write_state(moment):
    writer = factories.UserFactory(upn="kirjutaja@example.invalid")
    revoked = factories.UserFactory(upn="tyhistatud@example.invalid")
    reader = factories.UserFactory(upn="lugeja@example.invalid")
    for person in (writer, revoked, reader):
        support.enrolled(person, moment)
    run("grant-write", "--user", writer.upn)
    run("grant-write", "--user", revoked.upn)
    run("revoke-write", "--user", revoked.upn, "--reason", "x")

    lines = run("list").splitlines()

    assert lines[0].endswith("\tmuutmine")
    by_upn = {line.split("\t")[0]: line.split("\t")[-1] for line in lines[1:]}
    assert by_upn == {writer.upn: "jah", revoked.upn: "tühistatud", reader.upn: "ei"}
