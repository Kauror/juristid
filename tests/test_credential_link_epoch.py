"""One-time links are bound to the account's security epoch (docs/adr/0180 §5).

csf_42d9970ac23d6f55f4b19163: the password-reset link is minted by a job that
runs after the response, possibly much later. It used to issue a link for
whatever the account then was, so a job delayed past a password change put a
fresh, usable reset link in the mailbox for credentials that had already
moved on. These tests hold the binding — a link records the epoch it was issued
under and is refused once that moves — the job's own re-check, the database's
«one live link per person and purpose», and that the ordinary reset path still
works end to end with an in-memory outbox. Personal sign-in stays dormant: every
test here sets `local_password` for itself.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from django.core import mail as django_mail
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from app.accounts import administration, credentials, mail, tokens
from app.accounts.enums import CredentialTokenPurpose
from app.accounts.models import AccountCredentialToken, User
from app.audit.enums import AuthMethod, SecurityEventType
from app.audit.models import SecurityAuditEvent
from tests.local_auth import LINK_BASE, OTHER_PASSWORD, PASSWORD, apply_local_password, person

pytestmark = pytest.mark.django_db

RESET = CredentialTokenPurpose.PASSWORD_RESET


@pytest.fixture(autouse=True)
def _local_password(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    return apply_local_password(settings)


def _link_in(message: Any) -> str:
    match = re.search(rf"{re.escape(LINK_BASE)}(\S+)", message.body)
    assert match, message.body
    return match.group(1)


def _live(user: User) -> list[AccountCredentialToken]:
    return list(
        AccountCredentialToken.objects.filter(
            user=user, purpose=RESET, used_at__isnull=True, invalidated_at__isnull=True
        )
    )


def _bump(user: User) -> None:
    User.objects.filter(pk=user.pk).update(security_epoch=F("security_epoch") + 1)


@pytest.fixture
def queued(monkeypatch) -> list[Any]:
    """Hold the reset job instead of running it, as a slow worker would."""
    jobs: list[Any] = []
    monkeypatch.setattr(credentials, "after_the_response", jobs.append)
    return jobs


# -- the ordinary path ---------------------------------------------------------------


def test_the_reset_path_still_sends_one_link_that_sets_the_password(client):
    user, _ = person()

    client.post(reverse("accounts:forgot_password"), {"email": user.upn})
    assert len(django_mail.outbox) == 1
    target = client.get(_link_in(django_mail.outbox[0]))["Location"]
    client.post(target, {"password": OTHER_PASSWORD, "password_again": OTHER_PASSWORD})

    user.refresh_from_db()
    assert user.check_password(OTHER_PASSWORD)
    issued = AccountCredentialToken.objects.get(user=user, purpose=RESET)
    assert issued.used_at is not None
    assert issued.credential_epoch == 0


def test_a_link_records_the_epoch_it_was_issued_under():
    user, _ = person()
    _bump(user)
    _bump(user)

    link = tokens.issue(user=user, purpose=RESET)

    assert link.record.credential_epoch == 2


# -- the delayed job -------------------------------------------------------------------


def test_a_job_resumed_after_a_password_change_mints_no_link(queued):
    """Fails on 4818203e: the job issued and mailed a fresh, usable link."""
    user, _ = person()
    credentials.request_password_reset(_request(), email=user.upn)
    assert len(queued) == 1

    # The person remembers the password and changes it before the job runs.
    assert (
        credentials.change_password(
            _request(user), user=user, current=PASSWORD, new=OTHER_PASSWORD
        ).status
        == "changed"
    )
    queued[0]()

    assert _live(user) == []
    assert django_mail.outbox == []
    refused = SecurityAuditEvent.objects.get(
        event_type=SecurityEventType.PASSWORD_RESET_REQUESTED, detail__outcome="no_longer_due"
    )
    assert refused.succeeded is False


def test_a_job_resumed_after_the_account_was_switched_off_mints_no_link(queued):
    user, _ = person()
    admin, _ = person(manage=True, delegate=True, totp=True)
    credentials.request_password_reset(_request(), email=user.upn)

    administration.deactivate_account(actor=admin, user=user, reason="lahkus")
    queued[0]()

    assert _live(user) == []
    assert django_mail.outbox == []


def test_a_job_that_is_still_due_sends_its_link_under_the_requests_correlation(client, queued):
    user, _ = person()
    client.post(reverse("accounts:forgot_password"), {"email": user.upn})
    requested = SecurityAuditEvent.objects.get(
        event_type=SecurityEventType.PASSWORD_RESET_REQUESTED, detail__outcome="queued"
    )

    queued[0]()

    assert len(_live(user)) == 1
    assert len(django_mail.outbox) == 1
    delivery = SecurityAuditEvent.objects.get(event_type=SecurityEventType.ACCOUNT_EMAIL_DELIVERY)
    # Run outside the request, as the real worker thread is: the system's act,
    # tied to the request that queued it.
    assert requested.correlation_id is not None
    assert delivery.auth_method == AuthMethod.SYSTEM
    assert delivery.correlation_id == requested.correlation_id


def test_issue_refuses_an_epoch_that_moved():
    user, _ = person()
    _bump(user)

    with pytest.raises(tokens.StaleIssuance, match="security epoch changed"):
        tokens.issue(user=user, purpose=RESET, expected_epoch=0)

    assert _live(user) == []


# -- redemption ----------------------------------------------------------------------


def test_a_link_from_before_a_credential_change_is_refused_when_opened_and_when_spent():
    """Fails on 4818203e: a link the change's invalidation missed stayed good.

    The change here moves the epoch the way a concurrent password write does —
    without `invalidate_outstanding` having seen this link — which is the
    window issue-after-invalidate left open.
    """
    user, _ = person()
    link = tokens.issue(user=user, purpose=RESET)
    proof = tokens.proof_of(link.token)
    User.objects.filter(pk=user.pk).update(security_epoch=F("security_epoch") + 1)

    assert tokens.peek(proof, purpose=RESET) is None
    assert (
        credentials.set_password_with_link(proof=proof, purpose=RESET, password=OTHER_PASSWORD)
        is None
    )
    user.refresh_from_db()
    assert user.check_password(PASSWORD)
    record = AccountCredentialToken.objects.get(pk=link.record.pk)
    assert record.used_at is None


def test_a_mailed_link_opened_after_the_password_changed_sets_nothing(client):
    user, _ = person()
    client.post(reverse("accounts:forgot_password"), {"email": user.upn})
    query = parse_qs(urlparse(_link_in(django_mail.outbox[0])).query)
    proof = tokens.proof_of(query[mail.LINK_PARAMETER][0])
    assert tokens.peek(proof, purpose=RESET) is not None

    credentials.change_password(_request(user), user=user, current=PASSWORD, new=OTHER_PASSWORD)

    assert tokens.peek(proof, purpose=RESET) is None
    assert (
        credentials.set_password_with_link(
            proof=proof, purpose=RESET, password=PASSWORD + " kolmas"
        )
        is None
    )
    user.refresh_from_db()
    assert user.check_password(OTHER_PASSWORD)


# -- one live link -----------------------------------------------------------------


def test_reissuing_supersedes_and_leaves_one_live_link():
    user, _ = person()
    first = tokens.issue(user=user, purpose=RESET)
    second = tokens.issue(user=user, purpose=RESET)

    assert [token.pk for token in _live(user)] == [second.record.pk]
    assert tokens.peek(tokens.proof_of(first.token), purpose=RESET) is None


def test_the_database_refuses_a_second_live_link_for_one_purpose():
    user, _ = person()
    tokens.issue(user=user, purpose=RESET)

    with pytest.raises(IntegrityError, match="accounts_token_one_live_per_purpose"):
        with transaction.atomic():
            AccountCredentialToken.objects.create(
                user=user,
                purpose=RESET,
                selector="f" * 32,
                verifier_digest="0" * 64,
                expires_at=timezone.now() + timezone.timedelta(hours=1),
            )


def test_another_purpose_is_a_separate_live_link():
    user, _ = person()
    tokens.issue(user=user, purpose=RESET)
    tokens.issue(user=user, purpose=CredentialTokenPurpose.ACTIVATION)

    assert AccountCredentialToken.objects.filter(user=user, used_at__isnull=True).count() == 2


# -- the migration over rows ---------------------------------------------------------

BEFORE = ("accounts", "0004_local_authentication")
AFTER = ("accounts", "0005_credential_link_epoch_and_one_live")


def _migrate_to(target: tuple[str, str]) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target])


@pytest.fixture
def accounts_back_at_the_end():
    yield
    _migrate_to(AFTER)


def test_the_migration_supersedes_duplicate_live_links_and_keeps_the_newest(
    accounts_back_at_the_end,
):
    """The populated proof for `accounts/0005` (tests/test_migration_data_proof.py)."""
    user, _ = person()
    other, _ = person()
    _migrate_to(BEFORE)
    with connection.cursor() as cursor:
        for selector, minutes_ago, owner in (
            ("a" * 32, 30, user.pk),
            ("b" * 32, 20, user.pk),
            ("c" * 32, 10, user.pk),
            ("d" * 32, 10, other.pk),
        ):
            cursor.execute(
                "INSERT INTO accounts_accountcredentialtoken (id, created_at, updated_at, user_id,"
                " purpose, selector, verifier_digest, expires_at, invalidation_reason)"
                " VALUES (gen_random_uuid(), now() - make_interval(mins => %s), now(), %s,"
                " 'PASSWORD_RESET', %s, %s, now() + interval '1 hour', '')",
                [minutes_ago, owner, selector, "0" * 64],
            )

    _migrate_to(AFTER)

    rows = {
        row.selector: row
        for row in AccountCredentialToken.objects.filter(user__in=[user, other], purpose=RESET)
    }
    assert rows["c" * 32].invalidated_at is None
    assert rows["d" * 32].invalidated_at is None
    for older in ("a" * 32, "b" * 32):
        assert rows[older].invalidated_at is not None
        assert rows[older].invalidation_reason == "superseded_by_migration"
    assert {row.credential_epoch for row in rows.values()} == {0}
    assert len(rows) == 4


def _request(user: User | None = None) -> Any:
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.test import RequestFactory

    request = RequestFactory().post("/konto/parool/", REMOTE_ADDR="203.0.113.40")
    SessionMiddleware(lambda r: None).process_request(request)
    request.user = user if user is not None else AnonymousUser()
    return request
