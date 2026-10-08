"""Haldus → Kasutajad over HTTP (docs/adr/0145 §12).

The gate first — who gets a 404 — then what an administrator can do through the
page, and what a crafted request cannot. The services' own refusals are in
tests/test_local_auth_lifecycle.py; this file proves the pages put nothing
between them and the browser that the services would not.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.accounts import local_auth
from app.accounts.enums import Capability, ProvisioningState, UserRole
from app.accounts.forms import field_name
from app.accounts.models import User
from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.core.authorization import has_capability
from tests import factories
from tests.local_auth import PASSWORD, administrator, apply_local_password, code, person, sign_in

pytestmark = pytest.mark.django_db

LIST = "account_admin:list"


@pytest.fixture(autouse=True)
def _local_password(settings):
    return apply_local_password(settings)


def _detail(user):
    return reverse("account_admin:detail", kwargs={"pk": user.pk})


def _form(user, **changes):
    """The account form as the page renders it, then changed."""
    data = {"display_name": user.display_name, "role": user.role}
    from app.accounts import capabilities
    from app.core.authorization import account_capabilities

    held = account_capabilities(user)
    for capability in capabilities.ALL_CAPABILITIES:
        if capability in held:
            data[field_name(capability)] = "on"
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return data


# -- who gets a 404 --------------------------------------------------------------------


ALL_ROUTES = [
    ("account_admin:list", False),
    ("account_admin:create", False),
    ("account_admin:detail", True),
    ("account_admin:change_email", True),
]


@pytest.mark.parametrize(("route", "takes_pk"), ALL_ROUTES)
def test_a_signed_in_colleague_without_the_capability_gets_a_404(client, route, takes_pk):
    user, _ = person(role=UserRole.DEPARTMENT_HEAD)
    sign_in(client, user)
    kwargs = {"pk": user.pk} if takes_pk else {}

    assert client.get(reverse(route, kwargs=kwargs)).status_code == 404
    assert client.post(reverse(route, kwargs=kwargs), {}).status_code == 404


@pytest.mark.parametrize(
    "route",
    [
        "account_admin:send_link",
        "account_admin:cancel_invitation",
        "account_admin:deactivate",
        "account_admin:reactivate",
        "account_admin:reset_second_factor",
    ],
)
def test_every_action_is_404_without_the_capability(client, route):
    user, _ = person()
    target, _ = person()
    sign_in(client, user)

    response = client.post(reverse(route, kwargs={"pk": target.pk}))

    assert response.status_code == 404
    target.refresh_from_db()
    assert target.is_active


def test_an_administrator_without_a_second_factor_session_gets_nothing(client):
    """The capability alone is not enough: the session must have proved a second factor."""
    user, _ = person(manage=True, delegate=True)
    sign_in(client, user)  # lands on mandatory enrolment

    assert client.get(reverse(LIST)).status_code == 302
    session = client.session
    session.pop(local_auth.SESSION_ENROLMENT_REQUIRED, None)
    session.save()
    assert client.get(reverse(LIST)).status_code == 404


def test_anonymous_is_sent_to_sign_in(client):
    response = client.get(reverse(LIST))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:sign_in"))


# -- the list ---------------------------------------------------------------------------


def test_the_list_shows_every_account_and_searches(client):
    _admin, _ = administrator(client)
    factories.UserFactory(display_name="Leida Otsitav", upn="leida@koda.ee")
    factories.UserFactory(display_name="Teine Inimene", upn="teine@koda.ee")

    everything = client.get(reverse(LIST))
    found = client.get(reverse(LIST), {"q": "leida"})
    by_address = client.get(reverse(LIST), {"q": "teine@koda"})

    assert everything.status_code == 200
    assert "Leida Otsitav" in everything.content.decode()
    assert "Teine Inimene" in everything.content.decode()
    assert "Leida Otsitav" in found.content.decode()
    assert "Teine Inimene" not in found.content.decode()
    assert "Teine Inimene" in by_address.content.decode()


def test_the_list_filters_by_status(client):
    administrator(client)
    pending = factories.UserFactory(
        display_name="Ootel Konto",
        is_active=False,
        provisioning_state=ProvisioningState.PENDING,
    )

    page = client.get(reverse(LIST), {"seis": "ootel"}).content.decode()

    assert pending.display_name in page
    assert "Ootab kinnitust" in page


def test_no_raw_permission_identifier_reaches_the_page(client):
    _admin, _ = administrator(client)
    specialist, _ = person()

    for page in (client.get(reverse(LIST)), client.get(_detail(specialist))):
        text = page.content.decode()
        for identifier in ("accounts.manage", "accounts.delegate", "department.view_management"):
            # Identifiers appear only inside form field names, never as text.
            assert f">{identifier}<" not in text
            assert f" {identifier} " not in text


def test_the_bar_offers_haldus_only_to_an_administrator(client):
    administrator(client)
    assert reverse(LIST) in client.get(reverse("matters:my_work")).content.decode()

    colleague = Client()
    user, _ = person()
    sign_in(colleague, user)
    assert reverse(LIST) not in colleague.get(reverse("matters:my_work")).content.decode()


# -- creating and editing ---------------------------------------------------------------


def test_creating_an_account_through_the_page(client):
    _admin, _ = administrator(client)

    response = client.post(
        reverse("account_admin:create"),
        {"display_name": "Uus Kolleeg", "email": "uus.kolleeg@koda.ee", "role": "SPECIALIST"},
    )

    user = User.objects.get(upn="uus.kolleeg@koda.ee")
    assert response["Location"] == _detail(user)
    assert user.provisioning_state == ProvisioningState.PENDING and not user.is_active
    page = client.get(_detail(user)).content.decode()
    assert "Kinnita ja saada kutse" in page
    assert "Konto loodud" in page  # history


def test_a_refused_creation_says_why(client):
    administrator(client)

    response = client.post(
        reverse("account_admin:create"),
        {"display_name": "Väline", "email": "valine@gmail.com", "role": "SPECIALIST"},
    )

    assert response.status_code == 400
    assert "Lubatud on ainult Koja aadressid" in response.content.decode()


def test_granting_a_permission_through_the_page(client):
    _admin, _ = administrator(client)
    specialist, _ = person()

    response = client.post(
        _detail(specialist),
        _form(specialist, **{field_name(Capability.VIEW_DEPARTMENT_MANAGEMENT): "on"}),
    )

    assert response.status_code == 302
    specialist.refresh_from_db()
    assert has_capability(specialist, Capability.VIEW_DEPARTMENT_MANAGEMENT)
    history = client.get(_detail(specialist)).content.decode()
    assert "Õigused muudeti" in history
    assert "Osakonna juhtimisvaade" in history


def test_a_manager_cannot_forge_an_administrative_grant(client):
    """The toggle is disabled for them, and a crafted value for it is not even read."""
    _manager, _ = administrator(client, delegate=False)
    specialist, _ = person()

    response = client.post(
        _detail(specialist), _form(specialist, **{field_name(Capability.MANAGE_ACCOUNTS): "on"})
    )

    assert response.status_code == 302
    specialist.refresh_from_db()
    assert not has_capability(specialist, Capability.MANAGE_ACCOUNTS)
    assert specialist.capability_overrides == {}


def test_a_manager_cannot_change_an_administrator_at_all(client):
    manager, _ = administrator(client, delegate=False)
    other_admin, _ = person(manage=True, delegate=True)

    client.post(_detail(other_admin), _form(other_admin, role="READER"))

    other_admin.refresh_from_db()
    assert other_admin.role == UserRole.SPECIALIST
    assert (
        client.post(reverse("account_admin:deactivate", kwargs={"pk": other_admin.pk})).status_code
        == 302
    )
    other_admin.refresh_from_db()
    assert other_admin.is_active
    assert SecurityAuditEvent.objects.filter(
        event_type=SecurityEventType.ACCESS_REFUSED, actor=manager, subject_id=other_admin.pk
    ).exists()


def test_an_administrator_cannot_change_their_own_power_through_a_crafted_post(client):
    admin, _ = administrator(client)

    client.post(_detail(admin), _form(admin, role="DEPARTMENT_HEAD"))

    admin.refresh_from_db()
    assert admin.role == UserRole.SPECIALIST


def test_changes_need_a_recent_proof_of_identity(client, settings):
    admin, secret = administrator(client)
    specialist, _ = person()
    session = client.session
    session[local_auth.SESSION_STRONG_AT] = (
        timezone.now() - timedelta(seconds=settings.LOCAL_AUTH_REAUTH_SECONDS + 60)
    ).isoformat()
    session.save()

    asked = client.post(
        _detail(specialist),
        _form(specialist, **{field_name(Capability.VIEW_DEPARTMENT_MANAGEMENT): "on"}),
    )

    assert asked["Location"].startswith(reverse("accounts:reauthenticate"))
    specialist.refresh_from_db()
    assert specialist.capability_overrides == {}

    proved = client.post(
        reverse("accounts:reauthenticate"),
        {"password": PASSWORD, "code": code(secret, admin), "next": _detail(specialist)},
    )
    assert proved["Location"] == _detail(specialist)
    client.post(
        _detail(specialist),
        _form(specialist, **{field_name(Capability.VIEW_DEPARTMENT_MANAGEMENT): "on"}),
    )
    specialist.refresh_from_db()
    assert has_capability(specialist, Capability.VIEW_DEPARTMENT_MANAGEMENT)


def test_a_wrong_reauthentication_changes_nothing(client, settings):
    _admin, _ = administrator(client)
    session = client.session
    session[local_auth.SESSION_STRONG_AT] = (
        timezone.now() - timedelta(seconds=settings.LOCAL_AUTH_REAUTH_SECONDS + 60)
    ).isoformat()
    session.save()

    response = client.post(
        reverse("accounts:reauthenticate"), {"password": "vale vale vale vale", "code": "000000"}
    )

    assert response.status_code == 400
    assert local_auth.needs_reauthentication(_request_for(client))


def _request_for(client):
    from django.test import RequestFactory

    request = RequestFactory().get("/")
    request.session = client.session
    return request


def test_the_permission_change_reaches_the_persons_open_session(client):
    administrator(client)
    head, _ = person(role=UserRole.DEPARTMENT_HEAD)
    their_session = Client()
    sign_in(their_session, head)
    assert their_session.get(reverse("matters:my_work")).status_code == 200

    client.post(
        _detail(head),
        _form(head, **{field_name(Capability.VIEW_DEPARTMENT_MANAGEMENT): None}),
    )

    assert their_session.get(reverse("matters:my_work")).status_code == 302


def test_deactivating_and_reactivating_through_the_page(client):
    administrator(client)
    specialist, _ = person()

    client.post(
        reverse("account_admin:deactivate", kwargs={"pk": specialist.pk}), {"reason": "Lahkus"}
    )
    specialist.refresh_from_db()
    assert not specialist.is_active
    assert "Välja lülitatud" in client.get(_detail(specialist)).content.decode()

    client.post(reverse("account_admin:reactivate", kwargs={"pk": specialist.pk}))
    specialist.refresh_from_db()
    assert specialist.is_active


def test_the_invitation_through_the_page_never_shows_the_link(client):
    from django.core import mail

    administrator(client)
    pending = factories.UserFactory(
        upn="kutse@koda.ee",
        email="kutse@koda.ee",
        is_active=False,
        provisioning_state=ProvisioningState.PENDING,
    )

    response = client.post(
        reverse("account_admin:send_link", kwargs={"pk": pending.pk}), follow=True
    )

    body = response.content.decode()
    assert "Link saadeti" in body
    assert len(mail.outbox) == 1
    token = mail.outbox[0].body.split("kood=")[1].split()[0]
    assert token not in body
    pending.refresh_from_db()
    assert pending.provisioning_state == ProvisioningState.INVITED


def test_with_delivery_off_the_page_says_so(client, settings):
    settings.ACCOUNT_EMAIL_DELIVERY_ENABLED = False
    administrator(client)
    pending = factories.UserFactory(
        upn="kutse2@koda.ee",
        email="kutse2@koda.ee",
        is_active=False,
        provisioning_state=ProvisioningState.PENDING,
    )

    response = client.post(
        reverse("account_admin:send_link", kwargs={"pk": pending.pk}), follow=True
    )

    assert "välja lülitatud" in response.content.decode()


def test_changing_the_address_through_the_page(client):
    administrator(client)
    placeholder = factories.UserFactory(upn="ireen", email="")

    response = client.post(
        reverse("account_admin:change_email", kwargs={"pk": placeholder.pk}),
        {"email": "ireen.x@koda.ee"},
    )

    assert response["Location"] == _detail(placeholder)
    placeholder.refresh_from_db()
    assert placeholder.upn == "ireen.x@koda.ee"


def test_the_address_page_is_404_for_a_manager(client):
    administrator(client, delegate=False)
    specialist, _ = person()

    assert (
        client.get(reverse("account_admin:change_email", kwargs={"pk": specialist.pk})).status_code
        == 404
    )


def test_the_page_holds_no_secret(client):
    _admin, _ = administrator(client)
    specialist, secret = person(totp=True)

    page = client.get(_detail(specialist)).content.decode()

    assert secret not in page
    assert specialist.password not in page
    assert "taastekoode" in page
