"""What `/` chooses, in every mode this deployment runs in.

The root is a doorway and its only job is picking a destination, so this file
tests the choice rather than the pages. Since 2026-08-30 that choice is Minu
asjad for a person, and the persona selector for somebody who is behind the
shared door and has not said who they are (app/core/views.py, ADR 0016).

Every assertion follows the redirect and names the view that answered. A test
that stopped at the `Location` string would pass just as happily against a
route that 404s, which is the failure worth catching.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.accounts import shared_gate
from app.accounts.enums import AuthMode
from tests import factories

pytestmark = pytest.mark.django_db

PASSWORD = "seda-parooli-ei-ole-kusagil-mujal"  # noqa: S105


# -- A. a person is already authenticated ----------------------------------


def test_an_authenticated_person_lands_on_minu_asjad(client, specialist):
    client.force_login(specialist)

    response = client.get("/")
    assert response.status_code == 302
    assert response["Location"] == reverse("matters:my_work")

    followed = client.get("/", follow=True)
    assert followed.status_code == 200
    assert followed.resolver_match.view_name == "matters:my_work"
    assert followed.request["PATH_INFO"] == "/minu-asjad/"


def test_the_landing_page_is_that_persons_own_work(client, specialist, other_specialist):
    factories.MatterFactory(owner=specialist, title="Minu enda teema", is_open=True)
    factories.MatterFactory(owner=other_specialist, title="Kolleegi teema", is_open=True)
    client.force_login(specialist)

    body = client.get("/", follow=True).content.decode()
    assert "Minu enda teema" in body
    assert "Kolleegi teema" not in body


def test_the_department_head_gets_the_same_home(client, department_head):
    """No role-specific home pages. A person's main page is Minu asjad."""
    client.force_login(department_head)
    response = client.get("/", follow=True)
    assert response.resolver_match.view_name == "matters:my_work"


def test_a_non_business_role_gets_the_same_home_rather_than_a_new_one(client, administrator):
    """ADMINISTRATOR is not a business persona, and still needs no dashboard.

    It cannot be selected behind the shared gate at all (ADR 0034), and where it
    *is* authenticated the page it lands on is the same one — its own, empty.
    That is the honest answer, and it is why this change needs no role-specific
    home page: the rule is "a person's main page is Minu asjad", with no
    exceptions to keep in step with the roles.
    """
    client.force_login(administrator)
    response = client.get("/", follow=True)
    assert response.status_code == 200
    assert response.resolver_match.view_name == "matters:my_work"


def test_the_root_stays_a_doorway_and_never_renders_the_page_itself(client, specialist):
    """`/minu-asjad/` remains the canonical address, so bookmarks keep working."""
    client.force_login(specialist)
    response = client.get("/")
    assert response.status_code == 302
    assert response.content == b""


# -- C. nothing is authenticated and no gate has been passed ---------------


def test_an_anonymous_visitor_still_gets_the_doorway(client, settings):
    """`AUTH_MODE=none` is the developer laptop and CI. Unchanged."""
    settings.AUTH_MODE = AuthMode.NONE
    response = client.get("/")
    assert response.status_code == 200
    assert response.resolver_match.view_name == "core:home"


# -- B. the shared gate, with and without a persona ------------------------


@pytest.fixture
def gate_mode(settings):
    settings.AUTH_MODE = AuthMode.SHARED_GATE
    settings.SHARED_GATE_PASSWORD = PASSWORD
    settings.SHARED_GATE_MAX_ATTEMPTS = 5
    settings.SHARED_GATE_LOCKOUT_SECONDS = 300
    settings.SHARED_GATE_MAX_LOCKOUT_SECONDS = 3600
    settings.DEV_LOGIN_ENABLED = False
    settings.LOGIN_URL = "accounts:choose_persona"
    return settings


@pytest.fixture
def behind_the_gate(client, gate_mode):
    response = client.post(reverse("accounts:shared_gate"), {"password": PASSWORD})
    assert response.status_code == 302
    return client


def test_the_gate_with_no_persona_goes_to_the_selector(behind_the_gate):
    """The regression that matters in production: not Ülevaade, not Minu asjad.

    Minu asjad is one person's desk and nobody has been named yet; Ülevaade is a
    destination somebody chooses. The honest answer is to ask who is reading.
    """
    response = behind_the_gate.get("/")
    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:choose_persona")

    followed = behind_the_gate.get("/", follow=True)
    assert followed.status_code == 200
    assert followed.resolver_match.view_name == "accounts:choose_persona"
    assert not followed.wsgi_request.user.is_authenticated


def test_the_first_entry_journey_never_passes_through_ulevaade(client, gate_mode):
    """Password to persona to Minu asjad, following every redirect as a browser would."""
    marko = factories.UserFactory(display_name="Marko Näidisjurist")
    factories.MatterFactory(owner=marko, title="Marko oma teema", is_open=True)

    # 1-2. The root asks for the department password and shows nothing else.
    first = client.get("/", follow=True)
    assert first.resolver_match.view_name == "accounts:shared_gate"
    assert "Marko oma teema" not in first.content.decode()

    # 3-4. The correct password leads to the persona selector, not to a page.
    passed = client.post(reverse("accounts:shared_gate"), {"password": PASSWORD}, follow=True)
    assert passed.resolver_match.view_name == "accounts:choose_persona"
    assert str(marko.pk) in passed.content.decode()

    # 5-7. Choosing a person lands on that person's own Minu asjad.
    chosen = client.post(reverse("accounts:act_as"), {"user_id": str(marko.pk)}, follow=True)
    assert chosen.status_code == 200
    assert chosen.resolver_match.view_name == "matters:my_work"
    assert chosen.request["PATH_INFO"] == "/minu-asjad/"
    assert chosen.wsgi_request.user.pk == marko.pk
    assert "Marko oma teema" in chosen.content.decode()

    # No step of that journey was the department page.
    overview = reverse("matters:overview")
    for step in (first, passed, chosen):
        assert overview not in [url for url, _ in step.redirect_chain]


def test_an_already_selected_persona_goes_straight_to_minu_asjad(behind_the_gate):
    marko = factories.UserFactory(display_name="Marko Näidisjurist")
    behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(marko.pk)})

    response = behind_the_gate.get("/")
    assert response.status_code == 302
    assert response["Location"] == reverse("matters:my_work")

    followed = behind_the_gate.get("/", follow=True)
    assert followed.resolver_match.view_name == "matters:my_work"
    assert followed.wsgi_request.user.pk == marko.pk
    # Not the password form, not the selector, not the department page.
    assert [url for url, _ in followed.redirect_chain] == ["/minu-asjad/"]


# -- the department page is unchanged --------------------------------------
#
# Reversed through the name Ülevaade's route carried. Since ADR 0049 merged the
# two department pages that name resolves to `/osakond/` itself rather than to
# the compatibility redirect, so nothing here changed except where it lands.


def test_the_department_page_is_still_reachable_for_a_persona(behind_the_gate):
    marko = factories.UserFactory()
    behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(marko.pk)})

    response = behind_the_gate.get(reverse("matters:overview"))
    assert response.status_code == 200
    assert response.resolver_match.view_name == "matters:department"


def test_the_department_page_is_still_reachable_with_no_persona(behind_the_gate):
    """Its no-persona capability is exactly what this change must not remove."""
    owner = factories.UserFactory()
    factories.MatterFactory(owner=owner, title="Avalik teema kõigile", is_open=True)

    response = behind_the_gate.get(reverse("matters:overview"))
    assert response.status_code == 200
    assert not response.wsgi_request.user.is_authenticated
    assert "Avalik teema kõigile" in response.content.decode()


# -- the explicit department switch still lands where it did ---------------


def test_clearing_the_persona_lands_on_the_department_page_and_does_not_loop(behind_the_gate):
    """An explicit choice of *nobody* is not the same as never having chosen.

    The default home asks who is reading; this asks for the department on
    purpose, and must not be bounced back through `/` into the selector again.
    """
    marko = factories.UserFactory()
    behind_the_gate.post(reverse("accounts:act_as"), {"user_id": str(marko.pk)})

    response = behind_the_gate.post(reverse("accounts:act_as"), {"user_id": ""}, follow=True)
    assert response.status_code == 200
    assert response.resolver_match.view_name == "matters:department"
    assert not response.wsgi_request.user.is_authenticated
    assert shared_gate.has_passed(response.wsgi_request)
    assert reverse("accounts:choose_persona") not in [url for url, _ in response.redirect_chain]


def test_a_contextual_persona_switch_still_returns_to_the_page(behind_the_gate):
    """Only *default* entry moved. A switch from the bar is still contextual."""
    marko = factories.UserFactory()
    response = behind_the_gate.post(
        reverse("accounts:act_as"),
        {"user_id": str(marko.pk), "next": reverse("matters:overview")},
    )
    assert response["Location"] == reverse("matters:overview")


# -- the development sign-in reaches the same home -------------------------


def test_the_development_sign_in_lands_on_minu_asjad(client, settings, specialist):
    """One definition of the default destination, reached through `/`."""
    settings.AUTH_MODE = AuthMode.NONE
    settings.DEV_LOGIN_ENABLED = True
    settings.DEV_LOGIN_PIN = ""

    response = client.post(
        reverse("accounts:dev_login"), {"user_id": str(specialist.pk)}, follow=True
    )
    assert response.status_code == 200
    assert response.resolver_match.view_name == "matters:my_work"
    assert [url for url, _ in response.redirect_chain] == ["/", "/minu-asjad/"]
