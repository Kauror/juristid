"""The Haldus shell: sections, landing, the bar's item, Osakond, Süsteemi seis (docs/adr/0186)."""

from __future__ import annotations

import pytest
from django.test import RequestFactory
from django.urls import reverse

from app.accounts.enums import UserRole
from app.haldus import sections
from tests import factories
from tests.admin_session import (
    apply_admin_gate,
    choose,
    full_admin,
    pass_gate,
    technical_admin,
    verified,
)
from tests.local_auth import person

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _gate(settings):
    return apply_admin_gate(settings)


def _keys(response):
    return [section.key for section in response.context["haldus_sections"]]


def test_the_registry_is_the_contracted_one():
    assert [s.key for s in sections.SECTIONS] == [
        "ulevaade",
        "kasutajad",
        "klassifikaatorid",
        "osakond",
        "susteem",
        "lovable",
    ]
    assert sections.section("ulevaade").url_name == "haldus:index"
    assert reverse("haldus:index") == "/haldus/"


def test_a_section_whose_route_is_not_installed_stays_hidden(monkeypatch):
    request = RequestFactory().get("/haldus/")
    request.user = factories.DepartmentHeadFactory()
    request.session = {}
    monkeypatch.setattr(sections, "_route_exists", lambda name: False)
    assert not sections.may_open_reference_data(request)


def test_the_department_head_sees_department_work_only(client):
    head = factories.DepartmentHeadFactory()
    pass_gate(client)
    choose(client, head)

    page = client.get(reverse("haldus:index"))

    assert page.status_code == 200
    assert "ulevaade" in _keys(page) and "osakond" in _keys(page)
    assert "kasutajad" not in _keys(page) and "susteem" not in _keys(page)
    assert "Halduse järjepidevus" not in page.content.decode()
    osakond = client.get(reverse("haldus:osakond"))
    assert osakond.status_code == 200
    assert reverse("matters:department") in osakond.content.decode()


def test_a_head_holding_administration_is_offered_the_step_up_not_given_it(client):
    head, _ = full_admin(role=UserRole.DEPARTMENT_HEAD)
    pass_gate(client)
    choose(client, head)

    page = client.get(reverse("haldus:index")).content.decode()

    assert reverse("haldus:sign_in") in page
    assert reverse("account_admin:list") not in page


def test_the_verified_administrator_sees_continuity_and_their_session(client):
    admin, secret = technical_admin()
    full_admin()
    verified(client, admin, secret)

    page = client.get(reverse("haldus:index"))
    body = page.content.decode()

    assert _keys(page)[:2] == ["ulevaade", "kasutajad"] and "susteem" in _keys(page)
    assert "Halduse järjepidevus" in body and "Tagatud" in body
    assert admin.display_name in body
    assert reverse("haldus:sign_out") in body


def test_the_bar_offers_the_haldus_gear_to_every_persona(client):
    """Ülevaade opens for everybody signed in since 2026-10-28, so the gear does too."""
    specialist, _ = person()
    pass_gate(client)
    choose(client, specialist)
    assert 'href="/haldus/"' in client.get(reverse("matters:my_work")).content.decode()

    choose(client, factories.DepartmentHeadFactory())
    assert 'href="/haldus/"' in client.get(reverse("matters:my_work")).content.decode()


def test_the_bar_does_not_say_who_the_administrators_are(client):
    """Security review P3b: a persona's administrative standing changes nothing visible."""
    manager, _ = person(manage=True, delegate=True, totp=True)
    colleague, _ = person()
    pass_gate(client)

    def bar(persona):
        choose(client, persona)
        body = client.get(reverse("matters:my_work")).content.decode()
        start = body.index('<nav class="topnav"')
        return body[start : body.index("</nav>", start)]

    assert bar(manager) == bar(colleague)
    assert "/haldus/" not in bar(manager)


def test_the_page_offers_the_sign_in_to_every_head_alike(client):
    """From the persona menu now, on every page and the same for both."""
    plain = factories.DepartmentHeadFactory()
    admin, _ = full_admin(role=UserRole.DEPARTMENT_HEAD)
    pass_gate(client)
    choose(client, plain)
    plain_page = client.get(reverse("haldus:index")).content.decode()
    choose(client, admin)
    admin_page = client.get(reverse("haldus:index")).content.decode()

    assert reverse("haldus:sign_in") in plain_page
    assert reverse("haldus:sign_in") in admin_page


def test_the_persona_page_offers_the_administrator_sign_in(client):
    pass_gate(client)
    assert (
        reverse("haldus:sign_in") in client.get(reverse("accounts:choose_persona")).content.decode()
    )


def test_system_status_shows_counts_and_never_a_secret(client, settings):
    settings.LOCAL_AUTH_MFA_ENCRYPTION_KEY = "mfa-voti-mida-lehel-ei-tohi-olla-7781"
    admin, secret = technical_admin()
    verified(client, admin, secret)

    page = client.get(reverse("haldus:susteem"))
    body = page.content.decode()

    assert page.status_code == 200
    for label in ("Versioon", "Otsinguindeksi värskus", "Teksti eraldamine ebaõnnestus"):
        assert label in body
    for secret_value in (settings.LOCAL_AUTH_MFA_ENCRYPTION_KEY, settings.SHARED_GATE_PASSWORD):
        assert secret_value not in body
    assert "POSTGRES_PASSWORD" not in body


def test_system_status_is_not_for_an_administrator_without_the_technical_role(client):
    admin, secret = full_admin(role=UserRole.DEPARTMENT_HEAD)
    verified(client, admin, secret)
    assert client.get(reverse("haldus:susteem")).status_code == 404


def test_the_account_list_sits_inside_the_shell(client):
    admin, secret = full_admin()
    verified(client, admin, secret)
    page = client.get(reverse("account_admin:list"))
    assert page.context["haldus_active"] == "kasutajad"
    assert 'aria-label="Halduse osad"' in page.content.decode()
