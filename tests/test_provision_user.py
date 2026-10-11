"""Onboarding a real member of the department (ENG-013).

The runbook used to onboard every person with `createsuperuser`, which makes an
ADMINISTRATOR superuser: never offered as a persona, never assignable, and —
once authentication identifies people — the Django admin over everything. The
account a person needs is already defined by the rest of the application; these
hold `provision_user` to it, through the real command.

Addresses are under `.test` (RFC 2606): real-looking in shape, reserved, and
not the `.invalid` the synthetic worlds use and this command refuses.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from app.accounts.enums import AuthMode, UserRole
from app.accounts.models import User
from app.accounts.selectors import is_assignable_business_user, is_persona_candidate
from app.accounts.services import (
    PROVISIONING_REFUSED_UNDER_PERSONAL_SIGN_IN,
    provision_department_user,
)
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from tests import factories
from tests.gate import apply_shared_gate
from tests.local_auth import apply_local_password
from tests.refusals import refused

pytestmark = pytest.mark.django_db

UPN = "uus.jurist@koda.test"


def provision(*args: str) -> str:
    out = StringIO()
    call_command("provision_user", *args, stdout=out)
    return out.getvalue()


def test_the_account_is_a_department_worker_and_nothing_more() -> None:
    out = provision("--upn", UPN, "--display-name", "Uus Jurist")

    user = User.objects.get(upn=UPN)
    assert user.role == UserRole.SPECIALIST
    assert not user.is_staff
    assert not user.is_superuser
    assert not user.is_synthetic
    assert user.is_active
    assert not user.has_usable_password()
    assert user.entra_object_id is None
    assert is_persona_candidate(user)
    assert is_assignable_business_user(user)
    assert "Created Uus Jurist" in out


def test_a_department_head_can_be_provisioned() -> None:
    provision("--upn", UPN, "--display-name", "Uus Juht", "--role", UserRole.DEPARTMENT_HEAD)

    user = User.objects.get(upn=UPN)
    assert user.role == UserRole.DEPARTMENT_HEAD
    assert is_persona_candidate(user)


def test_provisioning_is_recorded_in_the_security_trail() -> None:
    provision("--upn", UPN, "--display-name", "Uus Jurist")

    user = User.objects.get(upn=UPN)
    event = SecurityAuditEvent.objects.get(
        event_type=SecurityEventType.ROLE_CHANGED, subject_id=str(user.pk)
    )
    assert event.detail["reason"] == "account_provisioned"
    assert event.detail["role"] == UserRole.SPECIALIST
    assert event.detail["previous_role"] is None


@pytest.mark.parametrize("role", [UserRole.ADMINISTRATOR, UserRole.READER, "OMANIK"])
def test_a_role_that_is_not_department_work_is_refused(role: str) -> None:
    with pytest.raises(CommandError):
        provision("--upn", UPN, "--display-name", "Keegi", "--role", role)
    assert not User.objects.filter(upn=UPN).exists()


def test_there_is_no_password_option() -> None:
    with pytest.raises(CommandError):
        provision("--upn", UPN, "--display-name", "Keegi", "--password", "salasõna")
    assert not User.objects.filter(upn=UPN).exists()


@pytest.mark.parametrize(
    "upn", ["uus.jurist", "uus jurist@koda.test", "jurist@example.invalid", "", "   "]
)
def test_an_identity_that_cannot_be_asserted_is_refused(upn: str) -> None:
    with pytest.raises(CommandError):
        provision("--upn", upn, "--display-name", "Keegi")
    assert User.objects.filter(is_synthetic=False, is_superuser=False).count() == 0


def test_a_display_name_is_required() -> None:
    with pytest.raises(CommandError):
        provision("--upn", UPN, "--display-name", "   ")
    assert not User.objects.filter(upn=UPN).exists()


def test_the_address_is_normalised_the_way_sign_in_matches_it() -> None:
    provision("--upn", "  Uus.Jurist@Koda.TEST ", "--display-name", "Uus Jurist")

    assert User.objects.filter(upn=UPN).exists()


def test_asking_again_for_the_same_account_changes_nothing() -> None:
    provision("--upn", UPN, "--display-name", "Uus Jurist")
    user = User.objects.get(upn=UPN)
    before = (user.updated_at, user.password)

    out = provision("--upn", UPN.upper(), "--display-name", "Uus Jurist")

    user.refresh_from_db()
    assert (user.updated_at, user.password) == before
    assert User.objects.filter(upn=UPN).count() == 1
    assert SecurityAuditEvent.objects.filter(subject_id=str(user.pk)).count() == 1
    assert "Nothing changed" in out


@pytest.mark.parametrize(
    ("args", "named"),
    [
        (("--display-name", "Keegi Teine"), "display name"),
        (("--display-name", "Uus Jurist", "--role", UserRole.DEPARTMENT_HEAD), "role"),
    ],
)
def test_an_existing_account_is_never_silently_changed(args: tuple[str, ...], named: str) -> None:
    provision("--upn", UPN, "--display-name", "Uus Jurist")

    with pytest.raises(CommandError) as refused:
        provision("--upn", UPN, *args)

    assert named in str(refused.value)
    user = User.objects.get(upn=UPN)
    assert (user.display_name, user.role) == ("Uus Jurist", UserRole.SPECIALIST)


@pytest.mark.parametrize(
    "existing",
    [
        {"is_staff": True, "is_superuser": True, "role": UserRole.ADMINISTRATOR},
        {"is_active": False},
        {"is_synthetic": True},
    ],
    ids=["superuser", "inactive", "synthetic"],
)
def test_an_existing_account_of_another_kind_is_refused_not_converted(existing: dict) -> None:
    """A superuser is not demoted, a leaver is not reactivated, by onboarding."""
    # Real unless the case says otherwise, so each case differs in one thing.
    account = factories.UserFactory(
        upn=UPN, display_name="Uus Jurist", **{"is_synthetic": False, **existing}
    )

    with pytest.raises(CommandError):
        provision("--upn", UPN, "--display-name", "Uus Jurist")

    account.refresh_from_db()
    for field, value in existing.items():
        assert getattr(account, field) == value


# -- the mode, and the address rule (AUD-051 AU-1) -------------------------------


@pytest.mark.parametrize(
    "existing", [False, True], ids=["new address", "identical account already there"]
)
def test_personal_sign_in_refuses_the_command_before_reading_anything(
    settings, existing: bool
) -> None:
    """Under `local_password` an account is invited and activated, never just made.

    An account this made would be ACTIVATED and active at once — no approval, no
    invitation, no password the person chose — beside a lifecycle that exists so
    that none of that happens (docs/adr/0145 §5). Refused even when the account
    already exists exactly so: the answer does not depend on what is there.
    """
    if existing:
        provision("--upn", UPN, "--display-name", "Uus Jurist")
    before = (User.objects.count(), SecurityAuditEvent.objects.count())
    apply_local_password(settings)

    with pytest.raises(CommandError) as refused_:
        provision("--upn", "teine.jurist@koda.ee", "--display-name", "Teine Jurist")

    assert str(refused_.value) == PROVISIONING_REFUSED_UNDER_PERSONAL_SIGN_IN
    assert "bootstrap_account_admin" in str(refused_.value)
    assert "Haldus → Kasutajad" in str(refused_.value)
    assert (User.objects.count(), SecurityAuditEvent.objects.count()) == before


def test_personal_sign_in_refuses_the_service_too(settings) -> None:
    """The refusal is the service's, so a shell session cannot step around the command."""
    apply_local_password(settings)

    with refused(PROVISIONING_REFUSED_UNDER_PERSONAL_SIGN_IN):
        provision_department_user(upn="uus.jurist@koda.ee", display_name="Uus Jurist")

    assert not User.objects.filter(upn="uus.jurist@koda.ee").exists()


@pytest.mark.parametrize("mode", [AuthMode.NONE, AuthMode.SHARED_GATE, AuthMode.CLOUDFLARE_ACCESS])
def test_every_other_mode_onboards_exactly_as_before(settings, mode: str) -> None:
    """Production runs the shared gate, and its onboarding must not move."""
    if mode == AuthMode.SHARED_GATE:
        apply_shared_gate(settings)
    else:
        settings.AUTH_MODE = mode

    provision("--upn", UPN, "--display-name", "Uus Jurist")

    user = User.objects.get(upn=UPN)
    assert user.is_active
    assert is_persona_candidate(user)


@pytest.mark.parametrize(
    "upn",
    [
        "uus.jurist+teine@koda.test",  # the same mailbox as uus.jurist@koda.test
        "uus.jurist@kõda.test",  # an internationalised domain: two spellings of one
    ],
    ids=["subaddress", "non-ascii"],
)
def test_a_second_spelling_of_a_mailbox_is_refused(upn: str) -> None:
    """The lifecycle's address rule (`email_policy.clean`), applied here too.

    Both of these pass Django's `validate_email`, which is all this used to
    ask; either would make a second identity for a mailbox that may already
    have one (docs/adr/0145 §4).
    """
    with refused("is not one unambiguous address"):
        provision_department_user(upn=upn, display_name="Keegi")

    assert User.objects.filter(is_synthetic=False, is_superuser=False).count() == 0


@pytest.mark.parametrize("upn", [UPN, "uus.jurist@naidiskoda.test", "uus.jurist@koda.ee"])
def test_the_domain_rule_is_the_personal_sign_in_lifecycles_not_this_commands(
    settings, upn: str
) -> None:
    """Under the gate and Access the domain is whatever their own door admits.

    `ACCOUNT_ALLOWED_EMAIL_DOMAINS` is a decision of personal sign-in, whose
    accounts this no longer makes; the gate's onboarding and every fixture that
    provisions a `.test` address go on working.
    """
    apply_shared_gate(settings)
    settings.ACCOUNT_ALLOWED_EMAIL_DOMAINS = ["koda.ee"]

    provision("--upn", upn, "--display-name", "Uus Jurist")

    assert User.objects.filter(upn=upn).exists()
