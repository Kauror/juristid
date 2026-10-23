"""How the two real administrators get their credentials under the shared gate (docs/adr/0186).

The operator's half — `bootstrap_account_admin` and `issue_administrator_link`,
on the host, to a terminal — and the person's half: following their own
one-time link behind the gate, choosing their own password, then their first
administrator sign-in with an authenticator they set up themselves. The link
pages answer under the gate only for somebody who may administer.
"""

from __future__ import annotations

import io
import re
import sys
from urllib.parse import urlsplit

import pyotp
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.accounts import admin_session, administration, mfa
from app.accounts.enums import ProvisioningState, UserRole
from app.accounts.models import User
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from tests import factories
from tests.admin_session import apply_admin_gate, full_admin, pass_gate
from tests.local_auth import LINK_BASE, PASSWORD, person

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _gate(settings):
    return apply_admin_gate(settings)


@pytest.fixture
def terminal(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)


def _command(name, **options):
    out = io.StringIO()
    call_command(name, stdout=out, **options)
    return out.getvalue()


def _printed_link(output: str) -> str:
    match = re.search(rf"{re.escape(LINK_BASE)}(\S+)", output)
    assert match, output
    return match.group(1)


def _set_password_through(client: Client, path: str, password: str = PASSWORD):
    """Follow a link path from outside: the gate first, then the page, then the form."""
    arriving = client.get(path)
    assert arriving.status_code == 302
    gate = arriving["Location"]
    assert gate.startswith(reverse("accounts:shared_gate"))
    assert "kood" not in gate  # the secret never travels in the gate's `next`
    from tests.gate import PASSWORD as GATE_PASSWORD

    after_gate = client.post(gate, {"password": GATE_PASSWORD})
    assert after_gate.status_code == 302
    page = after_gate["Location"]
    assert urlsplit(page).query == ""
    assert client.get(page).status_code == 200
    return client.post(page, {"password": password, "password_again": password})


def _enrol_and_open(client: Client, user: User):
    response = client.post(reverse("haldus:sign_in"), {"email": user.upn, "password": PASSWORD})
    assert response["Location"] == reverse("haldus:enrol")
    client.get(reverse("haldus:enrol"))
    sealed = client.session[admin_session.SESSION_SEALED_ENROLMENT]
    secret = mfa.resume_enrolment(user, sealed).secret
    confirmed = client.post(reverse("haldus:enrol"), {"code": pyotp.TOTP(secret).now()})
    assert confirmed.status_code == 200
    return secret


# -- the whole path, for two real-shaped administrators ---------------------------------------


def test_two_administrators_enrol_end_to_end_under_the_gate(terminal):
    printed = _command(
        "bootstrap_account_admin",
        email="tehniline.haldur@koda.ee",
        confirm_email="tehniline.haldur@koda.ee",
        display_name="Tehniline Haldur",
        role=UserRole.ADMINISTRATOR,
        note="Paigaldus, operaator Test",
        print_activation_link=True,
    )
    first = User.objects.get(upn="tehniline.haldur@koda.ee")
    assert first.provisioning_state == ProvisioningState.INVITED
    assert not first.has_usable_password()

    browser = Client()
    done = _set_password_through(browser, _printed_link(printed))
    assert done["Location"] == reverse("haldus:sign_in")
    first.refresh_from_db()
    assert first.is_active and first.has_local_password
    _enrol_and_open(browser, first)
    assert browser.get(reverse("account_admin:list")).status_code == 200
    assert administration.continuity_status().count == 1  # the bootstrap phase

    # The first administrator makes the department head a full administrator.
    head = factories.DepartmentHeadFactory(upn="osakonnajuht@koda.ee", email="osakonnajuht@koda.ee")
    from app.accounts.forms import field_name

    form = {
        "display_name": head.display_name,
        "role": head.role,
        field_name("department.view_management"): "on",
        field_name("work.assign"): "on",
        field_name("work_victory.review"): "on",
        field_name("matter.delete_permanently"): "on",
        field_name("accounts.manage"): "on",
        field_name("accounts.delegate"): "on",
    }
    saved = browser.post(reverse("account_admin:detail", kwargs={"pk": head.pk}), form)
    assert saved.status_code == 302
    head.refresh_from_db()
    assert admin_session.may_receive_administrator_link(head)
    # Mail is off beside the gate: the page says the operator hands the link over.
    response = browser.post(reverse("account_admin:send_link", kwargs={"pk": head.pk}), follow=True)
    assert "issue_administrator_link" in response.content.decode()

    printed = _command(
        "issue_administrator_link",
        email=head.upn,
        confirm_email=head.upn,
        note="Teine haldur, operaator Test",
    )
    second_browser = Client()
    _set_password_through(second_browser, _printed_link(printed))
    head.refresh_from_db()
    _enrol_and_open(second_browser, head)

    assert administration.continuity_status().satisfied
    issued = SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.CREDENTIAL_LINK_ISSUED, subject_id=head.pk
    )
    for row in issued:
        assert LINK_BASE not in str(row.detail) and "kood" not in str(row.detail)


# -- what the operator commands refuse ----------------------------------------------------


def test_the_link_command_refuses_somebody_who_may_not_administer(terminal):
    specialist, _ = person()
    with pytest.raises(CommandError, match=re.escape(administration.LINK_NOT_AN_ADMINISTRATOR)):
        _command(
            "issue_administrator_link",
            email=specialist.upn,
            confirm_email=specialist.upn,
            note="test",
        )


def test_the_link_command_refuses_a_switched_off_administrator(terminal):
    admin, _ = full_admin()
    User.objects.filter(pk=admin.pk).update(is_active=False)
    with pytest.raises(CommandError, match=re.escape(administration.LINK_NOT_AN_ADMINISTRATOR)):
        _command("issue_administrator_link", email=admin.upn, confirm_email=admin.upn, note="x")


def test_the_link_command_refuses_a_pending_account_nobody_approved(terminal):
    pending = factories.UserFactory(
        upn="ootel@koda.ee",
        is_active=False,
        provisioning_state=ProvisioningState.PENDING,
        capability_overrides={"accounts.manage": "allow", "accounts.delegate": "allow"},
    )
    with pytest.raises(CommandError, match=re.escape(administration.LINK_NOT_APPROVED)):
        _command("issue_administrator_link", email=pending.upn, confirm_email=pending.upn, note="x")
    pending.approved_at = timezone.now()
    pending.save(update_fields=["approved_at"])
    _command("issue_administrator_link", email=pending.upn, confirm_email=pending.upn, note="x")
    pending.refresh_from_db()
    assert pending.provisioning_state == ProvisioningState.INVITED


def test_the_link_command_writes_only_to_a_terminal():
    admin, _ = full_admin()
    with pytest.raises(CommandError, match="interactive terminal"):
        _command("issue_administrator_link", email=admin.upn, confirm_email=admin.upn, note="x")
    assert not SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.CREDENTIAL_LINK_ISSUED
    ).exists()


def test_an_administrator_with_a_password_gets_a_reset_link(terminal):
    admin, _ = full_admin()
    output = _command(
        "issue_administrator_link", email=admin.upn, confirm_email=admin.upn, note="unustas"
    )
    assert reverse("accounts:reset_password") in output


@pytest.mark.parametrize("mode", ["none", "cloudflare_access"])
def test_the_operator_commands_need_a_mode_with_link_pages(settings, terminal, mode):
    admin, _ = full_admin()
    settings.AUTH_MODE = mode
    with pytest.raises(CommandError, match=re.escape(administration.LINK_WRONG_MODE)):
        _command("issue_administrator_link", email=admin.upn, confirm_email=admin.upn, note="x")


# -- the link pages under the gate --------------------------------------------------------


def test_a_link_for_somebody_who_may_not_administer_is_a_404_under_the_gate():
    from app.accounts import tokens
    from app.accounts.enums import CredentialTokenPurpose

    specialist, _ = person(password=None)
    issued = tokens.issue(user=specialist, purpose=CredentialTokenPurpose.ACTIVATION)
    client = Client()
    pass_gate(client)

    client.get(f"{reverse('accounts:activate')}?kood={issued.token}")

    assert client.get(reverse("accounts:activate")).status_code == 404
    specialist.refresh_from_db()
    assert not specialist.has_local_password


def test_without_a_link_the_pages_are_still_absent_under_the_gate():
    client = Client()
    pass_gate(client)
    for name in ("accounts:activate", "accounts:reset_password"):
        assert client.get(reverse(name)).status_code == 404
        assert client.post(reverse(name), {}).status_code == 404
    for name in ("accounts:sign_in", "accounts:forgot_password", "accounts:security"):
        assert client.get(reverse(name)).status_code == 404
