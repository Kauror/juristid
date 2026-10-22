"""A one-time link is mailed after its transaction commits, never under a lock.

An independent review of docs/adr/0180 §5: the reset job held the account's row
lock — and `send_setup_link` the administration lock as well — for the whole
SMTP conversation, up to the transport's timeout. A slow mail server then held
up the person's sign-in, and every administration change, for as long.

The decision and the issue stay under the locks; the message is sent after the
commit. These tests send through a mail backend that, at the moment of
sending, asks a *second* database connection whether the account's row and the
administration lock are free (`NOWAIT` / `pg_try_advisory_lock`), and checks
that the sending thread is outside any transaction. A message that cannot be
sent ends its own link and, for an invitation, leaves the account PENDING.

Two connections at a time: this thread and the probe (docs: CI is the
authority for concurrency; the emulated local cluster takes three at most).
"""

from __future__ import annotations

import threading
from typing import Any, ClassVar

import pytest
from django.core.mail.backends.base import BaseEmailBackend
from django.db import OperationalError, connection, connections, transaction

from app.accounts import administration, credentials, tokens
from app.accounts.enums import CredentialTokenPurpose, ProvisioningState
from app.accounts.models import AccountCredentialToken, User
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from tests import factories
from tests.local_auth import apply_local_password, person

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)

BACKEND = f"{__name__}.ProbingBackend"


class Probe:
    """What the world looked like from outside, while a message was being sent."""

    user_id: ClassVar[Any] = None
    fail: ClassVar[bool] = False
    during: ClassVar[Any] = None
    sent: ClassVar[list[Any]] = []
    observations: ClassVar[list[dict[str, Any]]] = []

    @classmethod
    def reset(cls, user_id: Any, *, fail: bool = False, during: Any = None) -> None:
        cls.user_id = user_id
        cls.fail = fail
        cls.during = during
        cls.sent = []
        cls.observations = []


def _from_another_connection(user_id: Any) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def look() -> None:
        try:
            with transaction.atomic():
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            f"SELECT id FROM {User._meta.db_table} WHERE id = %s FOR UPDATE NOWAIT",  # noqa: S608
                            [user_id],
                        )
                    seen["row_free"] = True
                except OperationalError:
                    seen["row_free"] = False
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_try_advisory_lock(%s, %s)", list(administration._ADMINISTRATION_LOCK)
                )
                seen["administration_free"] = cursor.fetchone()[0]
                if seen["administration_free"]:
                    cursor.execute(
                        "SELECT pg_advisory_unlock(%s, %s)",
                        list(administration._ADMINISTRATION_LOCK),
                    )
            if Probe.during is not None:
                Probe.during()
        finally:
            connections.close_all()

    thread = threading.Thread(target=look)
    thread.start()
    thread.join(timeout=30)
    assert not thread.is_alive(), "the probe never finished"
    return seen


class ProbingBackend(BaseEmailBackend):
    def send_messages(self, email_messages: Any) -> int:
        seen = _from_another_connection(Probe.user_id)
        seen["sender_in_transaction"] = connection.in_atomic_block
        Probe.observations.append(seen)
        if Probe.fail:
            raise ConnectionError("the mail server went away")
        messages = list(email_messages or [])
        Probe.sent.extend(messages)
        return len(messages)


@pytest.fixture(autouse=True)
def _local_password(settings):
    apply_local_password(settings)
    settings.EMAIL_BACKEND = BACKEND
    return settings


def _links(user: User, purpose: str) -> list[AccountCredentialToken]:
    return list(AccountCredentialToken.objects.filter(user=user, purpose=purpose))


# -- the reset job -----------------------------------------------------------------


def test_the_reset_job_mails_with_no_lock_held():
    user, _ = person()
    Probe.reset(user.pk)

    credentials._send_reset_link(user.pk, tokens.credential_stamp(user))

    assert Probe.observations == [
        {"row_free": True, "administration_free": True, "sender_in_transaction": False}
    ]
    assert len(Probe.sent) == 1
    (link,) = _links(user, CredentialTokenPurpose.PASSWORD_RESET)
    assert link.used_at is None and link.invalidated_at is None


def test_a_reset_message_that_could_not_be_sent_ends_its_link():
    user, _ = person()
    Probe.reset(user.pk, fail=True)

    credentials._send_reset_link(user.pk, tokens.credential_stamp(user))

    (link,) = _links(user, CredentialTokenPurpose.PASSWORD_RESET)
    assert link.invalidated_at is not None
    assert link.invalidation_reason == "not_delivered:transport_error"
    assert Probe.sent == []


# -- an administrator's invitation -------------------------------------------------------


def _pending() -> User:
    return factories.UserFactory(
        upn="kutse.kolleeg@koda.ee",
        email="kutse.kolleeg@koda.ee",
        is_active=False,
        provisioning_state=ProvisioningState.PENDING,
    )


def _invited_events(user: User) -> list[SecurityAuditEvent]:
    return list(
        SecurityAuditEvent.objects.filter(
            event_type=SecurityEventType.ACCOUNT_INVITED, subject_id=user.pk
        )
    )


def test_an_invitation_is_mailed_with_neither_lock_held_and_then_recorded():
    admin, _ = person(manage=True, delegate=True, totp=True)
    pending = _pending()
    Probe.reset(pending.pk)

    outcome = administration.send_setup_link(actor=admin, user=pending)

    assert outcome.delivery.delivered
    assert Probe.observations == [
        {"row_free": True, "administration_free": True, "sender_in_transaction": False}
    ]
    pending.refresh_from_db()
    assert pending.provisioning_state == ProvisioningState.INVITED
    assert pending.approved_by_id == admin.pk
    assert [event.succeeded for event in _invited_events(pending)] == [True]


def test_an_invitation_that_could_not_be_sent_stays_pending_and_ends_its_link():
    admin, _ = person(manage=True, delegate=True, totp=True)
    pending = _pending()
    Probe.reset(pending.pk, fail=True)

    outcome = administration.send_setup_link(actor=admin, user=pending)

    assert not outcome.delivery.delivered
    pending.refresh_from_db()
    assert pending.provisioning_state == ProvisioningState.PENDING
    (link,) = _links(pending, CredentialTokenPurpose.ACTIVATION)
    assert link.invalidation_reason == "not_delivered:transport_error"
    assert [event.succeeded for event in _invited_events(pending)] == [False]


def test_a_link_ended_while_its_message_was_in_flight_does_not_mark_the_account_invited():
    admin, _ = person(manage=True, delegate=True, totp=True)
    pending = _pending()
    Probe.reset(
        pending.pk,
        during=lambda: tokens.invalidate_outstanding(user=pending, reason="invitation_cancelled"),
    )

    administration.send_setup_link(actor=admin, user=pending)

    pending.refresh_from_db()
    assert pending.provisioning_state == ProvisioningState.PENDING
