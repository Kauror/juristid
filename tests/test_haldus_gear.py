"""Haldus as a gear, Ülevaade for every reader, the step-up in the persona menu.

The owner's brief of 2026-10-28 moved how Haldus is reached and nothing it
authorizes (docs/adr/0186, amendment 2026-10-28):

* the everyday navigation is Minu asjad · Osakond · Teemad · Statistika, and
  Haldus is a gear beside «Välju» with the accessible name «Haldus»;
* Haldus → Ülevaade opens for every signed-in persona, because it carries
  Rakendus — and that grants nothing: every protected route still refuses;
* «Halduri sisselogimine» is at the bottom of the persona menu, the same for
  every reader; while a verified session is open the menu names it instead.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from app.accounts import admin_session
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
from tests.local_auth import apply_local_password, person, sign_in

pytestmark = pytest.mark.django_db

PROTECTED = ("haldus:osakond", "haldus:susteem", "haldus:lovable", "account_admin:list")


@pytest.fixture(autouse=True)
def _gate(settings):
    return apply_admin_gate(settings)


def _keys(response):
    return [section.key for section in response.context["haldus_sections"]]


def _header(body: str) -> str:
    return body[: body.index('<main class="app__main"')]


def _nav(body: str) -> str:
    start = body.index('<nav class="topnav"')
    return body[start : body.index("</nav>", start)]


def _gear(body: str) -> str:
    start = body.index('<a class="topbar__haldus')
    return body[start : body.index("</a>", start) + len("</a>")]


def _persona_menu(body: str) -> str:
    start = body.index('id="persona-menu"')
    return body[start : body.index('<form class="topbar__signout"', start)]


def _rakendus(body: str) -> str:
    start = body.index('aria-labelledby="rakendus"')
    return body[start : body.index("</section>", start)]


def _as(client: Client, persona) -> Client:
    pass_gate(client)
    choose(client, persona)
    return client


# -- the gear -----------------------------------------------------------------------


def test_haldus_is_a_gear_beside_valju_not_a_navigation_item(client):
    body = _as(client, person()[0]).get(reverse("matters:my_work")).content.decode()

    nav = _nav(body)
    assert ">Haldus<" not in nav and "/haldus/" not in nav
    for label in ("Minu asjad", "Osakond", "Teemad", "Statistika"):
        assert f">{label}</a>" in nav, label

    gear = _gear(body)
    assert 'href="/haldus/"' in gear and 'title="Haldus"' in gear
    assert '<span class="visually-hidden">Haldus</span>' in gear
    assert 'aria-hidden="true"' in gear  # the icon is decoration; the name is the text
    assert "aria-current" not in gear
    header = _header(body)
    after = header[header.index(gear) + len(gear) :]
    assert after.lstrip().startswith('<form class="topbar__signout"')


def test_the_gear_is_marked_current_on_a_haldus_page(client):
    body = _as(client, person()[0]).get(reverse("haldus:index")).content.decode()
    gear = _gear(body)
    assert 'aria-current="page"' in gear and "is-active" in gear
    assert 'aria-current="page"' not in _nav(body)


def test_every_persona_sees_the_same_bar_and_the_same_menu_footer(client):
    """Security review P3b, kept: a persona's administrative standing changes nothing visible."""
    manager, _ = full_admin()
    colleague, _ = person()
    pass_gate(client)

    def bar(persona):
        choose(client, persona)
        body = client.get(reverse("matters:my_work")).content.decode()
        menu_footer = _persona_menu(body).split('class="personamenu__note"', 1)[1]
        return _nav(body), _gear(body), menu_footer

    assert bar(manager) == bar(colleague) == bar(factories.DepartmentHeadFactory())


def test_under_personal_sign_in_the_gear_sits_beside_the_avatar(client, settings):
    apply_local_password(settings)
    user, _ = person()
    sign_in(client, user)

    header = _header(client.get(reverse("matters:my_work")).content.decode())

    assert reverse("accounts:profile") in header
    assert 'href="/haldus/"' in _gear(header)
    assert header.index(reverse("accounts:profile")) < header.index('class="topbar__haldus')
    # No step-up link: under personal sign-in the account itself is the administrator.
    assert reverse("haldus:sign_in") not in header


# -- the landing, for an ordinary reader ---------------------------------------------


@pytest.mark.parametrize(
    "factory",
    [factories.UserFactory, factories.DepartmentHeadFactory],
    ids=["specialist", "department-head"],
)
def test_an_ordinary_persona_opens_the_landing_and_nothing_privileged(client, factory):
    page = _as(client, factory()).get(reverse("haldus:index"))
    body = page.content.decode()

    assert page.status_code == 200
    assert "kasutajad" not in _keys(page) and "susteem" not in _keys(page)
    assert "lovable" not in _keys(page)
    for absent in ("Halduri seanss", "Halduse järjepidevus", "Kinnitatud isik"):
        assert absent not in body, absent
    for name in ("account_admin:list", "haldus:susteem", "haldus:lovable"):
        assert f'href="{reverse(name)}"' not in body, name
    assert admin_session.verified_administrator(page.wsgi_request) is None


def test_the_landing_grants_a_specialist_no_protected_route(client):
    """Each route asks its own question again; reaching Ülevaade answers none of them."""
    _as(client, factories.UserFactory())
    landing = client.get(reverse("haldus:index"))
    assert landing.status_code == 200
    # Ülevaade alone: no one-tab strip.
    assert 'aria-label="Halduse osad"' not in landing.content.decode()

    for name in PROTECTED:
        assert client.get(reverse(name)).status_code == 404, name
    assert client.post(reverse("haldus:lovable_check")).status_code == 404
    if sections._route_exists("reference_data:index"):
        assert client.get(reverse("reference_data:index")).status_code == 404


def test_a_signed_in_reader_opens_the_landing_and_no_protected_route(client, settings):
    """A READER is no persona behind the gate; signed in otherwise, it reads Rakendus."""
    settings.AUTH_MODE = "none"
    client.force_login(factories.ReaderFactory())

    page = client.get(reverse("haldus:index"))

    assert page.status_code == 200 and _keys(page) == ["ulevaade"]
    assert ">Rakendus</h2>" in page.content.decode()
    assert 'class="topbar__haldus' in page.content.decode()
    for name in PROTECTED:
        assert client.get(reverse(name)).status_code == 404, name


def test_a_persona_less_gate_session_does_not_reach_the_landing(client):
    """No persona and no verified administrator: nobody to show it to."""
    pass_gate(client)
    assert client.get(reverse("haldus:index")).status_code == 404


def test_an_anonymous_visitor_does_not_reach_the_landing(client, settings):
    settings.AUTH_MODE = "none"
    assert client.get(reverse("haldus:index")).status_code in {302, 404}


def test_the_landing_no_longer_carries_the_sign_in_call_to_action(client):
    """Moved to the persona menu; the route itself still answers."""
    body = _as(client, factories.DepartmentHeadFactory()).get(reverse("haldus:index"))
    content = body.content.decode()
    content = content[content.index('<main class="app__main"') :]

    assert 'id="halduri-kinnitus"' not in content
    assert reverse("haldus:sign_in") not in content
    assert client.get(reverse("haldus:sign_in")).status_code == 200


def test_the_verified_administrator_keeps_the_session_panel(client):
    admin, secret = technical_admin()
    verified(client, admin, secret)

    body = client.get(reverse("haldus:index")).content.decode()

    assert "Halduri seanss" in body and admin.display_name in body
    assert f'action="{reverse("haldus:sign_out")}"' in body
    assert ">Rakendus</h2>" in body


# -- Rakendus -----------------------------------------------------------------------


def test_rakendus_links_uuendused_and_kiirklahvid_and_both_resolve(client):
    section = _rakendus(
        _as(client, factories.UserFactory()).get(reverse("haldus:index")).content.decode()
    )

    assert ">Rakendus</h2>" in section and "Rakenduse versioon" in section
    for name, label in (
        ("core:release_notes", "Uuendused"),
        ("core:keyboard_shortcuts", "Kiirklahvid"),
    ):
        assert f'<a href="{reverse(name)}">{label}</a>' in section
        assert client.get(reverse(name)).status_code == 200, name


def test_rakendus_offers_the_block_reset_as_a_post_to_its_endpoint(client):
    section = _rakendus(
        _as(client, factories.UserFactory()).get(reverse("haldus:index")).content.decode()
    )
    start = section.index(f'action="{reverse("accounts:reset_disclosures")}"')
    form = section[section.rindex("<form", 0, start) : section.index("</form>", start)]

    assert 'method="post"' in form
    assert 'name="csrfmiddlewaretoken"' in form
    assert f'name="next" value="{reverse("haldus:index")}"' in form
    assert "Taasta plokkide vaikeseaded" in form


def test_a_persona_less_administrator_is_not_offered_the_block_reset(client):
    admin, secret = technical_admin()
    verified(client, admin, secret)
    section = _rakendus(client.get(reverse("haldus:index")).content.decode())
    assert reverse("accounts:reset_disclosures") not in section


# -- the persona menu ---------------------------------------------------------------


def test_the_persona_menu_offers_the_administrator_sign_in_to_everybody(client):
    pass_gate(client)
    for persona in (factories.UserFactory(), full_admin()[0]):
        choose(client, persona)
        menu = _persona_menu(client.get(reverse("matters:my_work")).content.decode())
        assert f'<a class="personamenu__adminlink" href="{reverse("haldus:sign_in")}">' in menu
        assert ">Halduri sisselogimine<" in menu
        # Choosing is still said not to be signing in.
        assert "Valik ei ole autentimine" in menu


def test_a_persona_less_gate_session_finds_the_sign_in_in_the_menu(client):
    pass_gate(client)
    menu = _persona_menu(client.get(reverse("accounts:choose_persona")).content.decode())
    assert f'href="{reverse("haldus:sign_in")}"' in menu


def test_choosing_an_administrator_persona_authenticates_nobody(client):
    """The menu's sign-in is the step-up; the persona above it is only a view."""
    admin, _ = full_admin()
    page = _as(client, admin).get(reverse("haldus:index"))

    assert admin_session.verified_administrator(page.wsgi_request) is None
    assert "kasutajad" not in _keys(page)
    assert "Halduri seanss:" not in _persona_menu(page.content.decode())
    assert client.get(reverse("account_admin:list")).status_code == 404


def test_the_persona_menu_names_the_verified_session_and_ends_it(client):
    admin, secret = full_admin()
    verified(client, admin, secret)
    choose(client, factories.UserFactory())

    menu = _persona_menu(client.get(reverse("matters:my_work")).content.decode())
    assert "Halduri seanss:" in menu and admin.display_name in menu
    assert f'action="{reverse("haldus:sign_out")}"' in menu
    assert f'href="{reverse("haldus:sign_in")}"' not in menu

    assert client.post(reverse("haldus:sign_out")).status_code == 302
    menu = _persona_menu(client.get(reverse("matters:my_work")).content.decode())
    assert "Halduri seanss:" not in menu
    assert f'href="{reverse("haldus:sign_in")}"' in menu
    # Ending it left the persona chosen.
    assert client.get(reverse("matters:my_work")).status_code == 200
