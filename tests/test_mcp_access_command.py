"""`manage.py mcp_access`: grant, link, revoke, list and unlock, by an operator (ADR 0185 §3)."""

from __future__ import annotations

import io
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, transaction
from django.utils import timezone

from app.accounts.models import User
from app.core.errors import ImmutableRecordError
from app.mcp_access import services
from app.mcp_access.models import (
    GrantStatus,
    McpAccessEvent,
    McpAccessEventType,
    McpAccessGrant,
    McpEnrolmentLink,
    McpToken,
)
from app.mcp_access.verification import verify_access_token
from tests import factories
from tests import mcp_access_support as support

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def mcp_on(settings):
    support.enable(settings)


@pytest.fixture
def moment(monkeypatch):
    return support.freeze_totp(monkeypatch)


def run(*args: str) -> str:
    out = io.StringIO()
    call_command("mcp_access", *args, stdout=out)
    return out.getvalue()


def test_grant_creates_a_pending_grant_and_prints_a_one_time_link():
    user = factories.UserFactory(upn="mari@example.invalid")

    output = run("grant", "--user", "MARI@example.invalid")

    grant = McpAccessGrant.objects.get(user=user)
    assert grant.status == GrantStatus.PENDING_ENROLMENT
    assert "manage.py mcp_access grant" in grant.created_via and grant.created_by is None
    link_line = next(line for line in output.splitlines() if line.startswith("https://"))
    assert link_line.startswith(f"{support.ORIGIN}/mcp-ligipaas/liitu/?kood=")
    verifier = link_line.rsplit(".", 1)[1]
    link = McpEnrolmentLink.objects.get(grant=grant)
    assert verifier not in link.verifier_digest and link.selector in link_line
    event = McpAccessEvent.objects.get(event_type=McpAccessEventType.GRANT_CREATED)
    assert "os-user=" in event.provenance and "host=" in event.provenance


def test_a_reader_may_be_granted_and_is_told_no_persona_is_needed():
    reader = factories.ReaderFactory()
    output = run("grant", "--user", reader.upn)
    assert McpAccessGrant.objects.filter(user=reader).exists()
    # docs/adr/0185 §3: a READER is never a persona, and needs none to enrol.
    assert "jagatud värava taga" in output
    assert "kasutajat (personat) valima ei pea" in output
    assert "e-posti aadressi" in output
    assert len([line for line in output.splitlines() if line.startswith("https://")]) == 1


def test_a_technical_account_with_a_business_role_is_refused():
    user = factories.UserFactory(is_staff=True)
    with pytest.raises(CommandError, match="tehnilisele kontole"):
        run("grant", "--user", user.upn)
    assert not McpAccessGrant.objects.filter(user=user).exists()


@pytest.mark.parametrize(
    ("make", "words"),
    [
        (lambda: factories.UserFactory(is_active=False), "ei ole aktiivne"),
        (lambda: factories.AdministratorFactory(), "sisu lugemise rolli"),
    ],
    ids=["inactive", "administrator"],
)
def test_grant_refuses_an_account_that_may_not_read(make, words):
    user = make()
    with pytest.raises(CommandError, match=words):
        run("grant", "--user", user.upn)
    assert not McpAccessGrant.objects.exists()
    assert McpAccessEvent.objects.filter(event_type=McpAccessEventType.GRANT_REFUSED).exists()


def test_grant_refuses_a_synthetic_account_where_real_data_is_allowed(settings):
    settings.REAL_DATA_ALLOWED = True
    user = factories.UserFactory(is_synthetic=True)
    with pytest.raises(CommandError, match="sünteetilisele"):
        run("grant", "--user", user.upn)


def test_grant_refuses_a_second_open_grant():
    user = factories.UserFactory()
    run("grant", "--user", user.upn)
    with pytest.raises(CommandError, match="juba kehtiv"):
        run("grant", "--user", user.upn)
    assert McpAccessGrant.objects.filter(user=user).count() == 1


def test_the_database_holds_one_open_grant_per_account_whatever_the_code_does():
    user = factories.UserFactory()
    McpAccessGrant.objects.create(user=user)
    with pytest.raises(DatabaseError), transaction.atomic():
        McpAccessGrant.objects.create(user=user, status=GrantStatus.ACTIVE)


def test_an_unknown_account_is_refused():
    with pytest.raises(CommandError, match="Sellist kontot ei ole"):
        run("grant", "--user", "nobody@example.invalid")


def test_enrolment_link_replaces_the_outstanding_link():
    user = factories.UserFactory()
    first = run("grant", "--user", user.upn)
    second = run("enrolment-link", "--user", user.upn)
    assert first != second
    assert McpEnrolmentLink.objects.filter(invalidated_at__isnull=False).count() == 1
    assert McpEnrolmentLink.objects.filter(invalidated_at__isnull=True).count() == 1


def test_enrolment_link_needs_a_pending_grant(moment):
    user = factories.UserFactory()
    support.enrolled(user, moment)
    with pytest.raises(CommandError, match="seadistamist ootavat"):
        run("enrolment-link", "--user", user.upn)


def test_revoke_ends_the_grant_and_every_token_at_once(moment):
    user = factories.UserFactory()
    _, secret = support.enrolled(user, moment)
    issued = support.tokens_for(user, secret, moment)

    output = run("revoke", "--user", user.upn, "--reason", "lahkus osakonnast")

    assert "Tühistatud võtmeid: 2" in output
    assert verify_access_token(issued.access_token) is None
    grant = McpAccessGrant.objects.get(user=user)
    assert grant.status == GrantStatus.REVOKED and grant.revoke_reason == "lahkus osakonnast"
    # A revoked grant can be followed by a new decision, enrolled afresh.
    run("grant", "--user", user.upn)
    assert McpAccessGrant.objects.filter(user=user).count() == 2


def test_revoke_all_is_the_kill_switch(moment):
    first = factories.UserFactory()
    second = factories.UserFactory()
    _, secret_one = support.enrolled(first, moment)
    _, secret_two = support.enrolled(second, moment)
    one = support.tokens_for(first, secret_one, moment)
    two = support.tokens_for(second, secret_two, moment)

    output = run("revoke-all", "--reason", "intsident")

    assert "Ligipääse: 2" in output
    assert verify_access_token(one.access_token) is None
    assert verify_access_token(two.access_token) is None
    assert not McpToken.objects.filter(revoked_at__isnull=True).exists()
    assert not McpAccessGrant.objects.exclude(status=GrantStatus.REVOKED).exists()


def test_revoking_needs_a_reason():
    user = factories.UserFactory()
    run("grant", "--user", user.upn)
    with pytest.raises(CommandError, match="põhjus"):
        run("revoke", "--user", user.upn, "--reason", "  ")


def test_list_shows_every_grant_and_no_secret(moment):
    user = factories.UserFactory(upn="loetelu@example.invalid")
    _, secret = support.enrolled(user, moment)
    issued = support.tokens_for(user, secret, moment)
    pending = factories.UserFactory(upn="ootel@example.invalid")
    run("grant", "--user", pending.upn)

    output = run("list")

    assert "loetelu@example.invalid\tACTIVE\tjah\t2\t" in output
    assert "ootel@example.invalid\tPENDING_ENROLMENT\tei\t0\t-" in output
    for value in (issued.access_token, issued.refresh_token, secret):
        assert value not in output
    for digest in McpToken.objects.values_list("digest", flat=True):
        assert digest not in output
    assert "kood=" not in output


def test_unlock_clears_a_wrong_code_lock(moment):
    user = factories.UserFactory()
    grant, _ = support.enrolled(user, moment)
    McpAccessGrant.objects.filter(pk=grant.pk).update(
        locked_until=timezone.now() + timedelta(minutes=15), failed_attempts=3
    )
    assert "Lukk eemaldatud" in run("unlock", "--user", user.upn)
    grant.refresh_from_db()
    assert grant.locked_until is None and grant.failed_attempts == 0
    assert McpAccessEvent.objects.filter(event_type=McpAccessEventType.GRANT_UNLOCKED).exists()


def test_with_mcp_off_only_list_and_revoking_run(settings, moment):
    user = factories.UserFactory()
    support.enrolled(user, moment)
    settings.MCP_ENABLED = False
    for args in (("grant", "--user", user.upn), ("enrolment-link", "--user", user.upn)):
        with pytest.raises(CommandError, match="MCP_ENABLED"):
            run(*args)
    with pytest.raises(CommandError, match="MCP_ENABLED"):
        run("unlock", "--user", user.upn)
    assert user.upn in run("list")
    run("revoke", "--user", user.upn, "--reason", "välja lülitatud")
    run("revoke-all", "--reason", "välja lülitatud")


def test_an_issuer_is_needed_to_print_a_link(settings):
    settings.JURISTID_PUBLIC_ORIGIN = ""
    user = factories.UserFactory()
    with pytest.raises(CommandError, match="JURISTID_PUBLIC_ORIGIN"):
        run("grant", "--user", user.upn)


def test_the_audit_trail_is_append_only_in_python_and_in_the_database():
    user = factories.UserFactory()
    run("grant", "--user", user.upn)
    event = McpAccessEvent.objects.first()
    with pytest.raises(ImmutableRecordError):
        event.save()
    with pytest.raises(DatabaseError), transaction.atomic():
        McpAccessEvent.objects.filter(pk=event.pk).update(provenance="muudetud")
    with pytest.raises(DatabaseError), transaction.atomic():
        McpAccessEvent.objects.filter(pk=event.pk).delete()


def test_the_grant_service_refuses_by_the_same_rules_as_the_command():
    with pytest.raises(services.GrantRefused, match="ei ole aktiivne"):
        services.grant_access(
            user=factories.UserFactory(is_active=False), provenance=support.OPERATOR
        )
    assert User.objects.count() == 1
