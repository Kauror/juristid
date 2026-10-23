"""Klassifikaatorid through a verified administrator session (docs/adr/0186, 0187).

A technical administrator holds no business role, so under the shared gate the
persona list never offers them and `request.user` can never be their account.
Granted `reference.manage`, they reach the reference-data pages through their
own verified session instead, and what they change is audited against that
principal. Without the grant, or without the session, nothing opens.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from app.accounts import capabilities
from app.accounts.enums import Capability, UserRole
from app.audit.enums import AuthMethod, ChangeEventType
from app.audit.models import ChangeEvent
from app.organisations import administration as catalogue
from app.organisations.models import AliasType
from tests import factories
from tests.admin_session import apply_admin_gate, choose, pass_gate, technical_admin, verified

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _gate(settings: Any) -> Any:
    return apply_admin_gate(settings)


def _grant_reference(user: Any) -> Any:
    overrides = dict(user.capability_overrides or {})
    overrides[Capability.MANAGE_REFERENCE_DATA.value] = capabilities.ALLOW
    user.capability_overrides = overrides
    user.save(update_fields=["capability_overrides"])
    return user


def test_a_granted_technical_administrator_works_without_a_persona() -> None:
    admin, secret = technical_admin()
    _grant_reference(admin)
    body = factories.OrganisationFactory(name="Vana Ametinimi")
    client = verified(Client(), admin, secret)

    assert client.get(reverse("reference_data:index")).status_code == 200
    response = client.post(
        reverse("reference_data:organisation_rename", kwargs={"pk": body.pk}),
        {
            "name": "Uus Ametinimi",
            "keep_old_name": "on",
            "revision": catalogue.organisation_revision(body),
        },
    )

    assert response.status_code == 302
    body.refresh_from_db()
    assert body.name == "Uus Ametinimi"
    assert body.aliases.get().alias_type == AliasType.HISTORICAL_NAME
    event = ChangeEvent.objects.get(event_type=ChangeEventType.ORGANISATION_RENAMED)
    assert event.actor == admin
    assert event.auth_method == AuthMethod.ADMINISTRATOR_SESSION


def test_the_klassifikaatorid_section_is_offered_to_the_granted_administrator() -> None:
    admin, secret = technical_admin()
    _grant_reference(admin)
    client = verified(Client(), admin, secret)

    page = client.get(reverse("haldus:index")).content.decode()

    assert reverse("reference_data:index") in page


def test_a_technical_administrator_without_the_grant_reaches_nothing() -> None:
    admin, secret = technical_admin()
    body = factories.OrganisationFactory(name="Puutumata Amet")
    client = verified(Client(), admin, secret)

    # Without a persona it meets the persona list like any other page; it is
    # never the reference page, and nothing is changed either way.
    assert client.get(reverse("reference_data:index")).status_code in {302, 404}
    response = client.post(
        reverse("reference_data:organisation_rename", kwargs={"pk": body.pk}),
        {"name": "Muudetud", "revision": catalogue.organisation_revision(body)},
    )

    assert response.status_code in {302, 404}
    body.refresh_from_db()
    assert body.name == "Puutumata Amet"


def test_the_grant_without_a_verified_session_opens_nothing() -> None:
    """Holding the capability is not proving to be its holder."""
    admin, _ = technical_admin()
    _grant_reference(admin)
    client = pass_gate(Client())

    assert client.get(reverse("reference_data:index")).status_code in {302, 404}


def test_a_persona_without_the_capability_gains_nothing_from_the_grant_of_another() -> None:
    """A specialist persona chosen beside nobody's session stays a specialist."""
    admin, _ = technical_admin()
    _grant_reference(admin)
    specialist = factories.UserFactory(role=UserRole.SPECIALIST)
    client = pass_gate(Client())
    choose(client, specialist)

    assert client.get(reverse("reference_data:index")).status_code == 404


def test_a_granted_administrator_sees_no_usage_counts() -> None:
    """Usage is business information; the administrator manages words, not work."""
    admin, secret = technical_admin()
    _grant_reference(admin)
    tag = factories.TagFactory(name_et="Andmekaitse")
    client = verified(Client(), admin, secret)

    response = client.get(reverse("reference_data:tag", kwargs={"pk": tag.pk}))

    assert response.status_code == 200
    assert response.context["shows_usage"] is False
