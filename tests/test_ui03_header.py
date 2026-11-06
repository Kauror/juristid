"""UI-03: the top bar's right-hand order, and «Seaded» behind the gear.

The owner's brief of 2026-11-06, as rendered HTML in every sign-in mode:

* the right-hand controls read search → «+ Uus teema» → whose work is on
  screen → the bell → settings → «Välju», and «Välju» is the last thing on the
  bar;
* the light/dark switch is inside the settings menu and nowhere else — no
  sun/moon button on the bar, signed in or not;
* the settings menu holds the switch, «Teavituste seaded» and the Haldus entry
  the gear used to be, each offered on its own existing terms;
* persona selection, and the avatar under personal sign-in, stay on the bar
  itself rather than moving into the menu.

What a browser does with it — opening, closing, focus, widths — is in
e2e/test_ui03_header.py.
"""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from tests import factories
from tests.admin_session import apply_admin_gate, choose, pass_gate
from tests.local_auth import apply_local_password, person, sign_in

pytestmark = pytest.mark.django_db

SETTINGS_URL = reverse("notifications:settings")

#: A marker each control renders exactly once, in the order the bar shows them.
ORDER = [
    ("search", 'class="searchfield"'),
    ("uus teema", "topbar__cta"),
    ("persona", None),  # depends on the mode, see `_persona_marker`
    ("bell", "notifbell"),
    ("settings", "data-settings-menu"),
    ("välju", 'class="topbar__signout"'),
]


def _header(body: str) -> str:
    return body[body.index('<header class="topbar">') : body.index("</header>")]


def _menu(body: str) -> str:
    start = body.index('<details class="settingsmenu')
    return body[start : body.index("</details>", start) + len("</details>")]


def _outside_menu(body: str) -> str:
    header = _header(body)
    menu = _menu(body)
    return header.replace(menu, "")


def _assert_order(header: str, persona_marker: str) -> None:
    positions = []
    for name, marker in ORDER:
        marker = marker or persona_marker
        assert header.count(marker) >= 1, f"{name} ({marker}) is not on the bar"
        positions.append((header.index(marker), name))
    assert [name for _, name in sorted(positions)] == [name for name, _ in ORDER], positions
    # Nothing after «Välju»: the form is the last control on the bar.
    tail = header[header.index('class="topbar__signout"') :]
    tail = tail[tail.index("</form>") + len("</form>") :]
    assert re.sub(r"\s|</div>", "", tail) == "", tail


# -- the order, in each mode ------------------------------------------------------------


def test_the_order_under_the_shared_gate(settings):
    apply_admin_gate(settings)
    client = pass_gate(Client())
    choose(client, factories.UserFactory())
    body = client.get(reverse("matters:my_work")).content.decode()
    _assert_order(_header(body), 'class="personaswitch"')


def test_the_order_under_personal_sign_in_keeps_the_avatar_on_the_bar(client, settings):
    apply_local_password(settings)
    user, _ = person()
    sign_in(client, user)
    body = client.get(reverse("matters:my_work")).content.decode()
    _assert_order(_header(body), "avatar avatar--link")
    # Directly on the bar, not behind the gear.
    outside = _outside_menu(body)
    assert f'href="{reverse("accounts:profile")}"' in outside
    assert reverse("accounts:profile") not in _menu(body)


def test_the_order_when_signed_in_otherwise(client, specialist):
    client.force_login(specialist)
    body = client.get(reverse("matters:my_work")).content.decode()
    _assert_order(_header(body), 'class="avatar"')


def test_persona_selection_stays_on_the_bar_not_in_the_menu(settings):
    apply_admin_gate(settings)
    client = pass_gate(Client())
    choose(client, factories.UserFactory())
    body = client.get(reverse("matters:my_work")).content.decode()
    assert "data-persona-trigger" in _outside_menu(body)
    assert "data-persona-trigger" not in _menu(body)
    assert reverse("accounts:act_as") not in _menu(body)


# -- the theme switch ---------------------------------------------------------------


def test_there_is_no_sun_and_moon_button_on_the_bar_once_signed_in(client, specialist):
    client.force_login(specialist)
    body = client.get(reverse("matters:my_work")).content.decode()
    assert body.count("data-theme-toggle") == 1
    assert "data-theme-toggle" in _menu(body)
    assert "data-theme-toggle" not in _outside_menu(body)


def test_there_is_no_sun_and_moon_button_on_the_bar_before_signing_in(settings):
    """The sign-in page keeps the switch — in the same gear, and only there."""
    apply_admin_gate(settings)
    body = Client().get(reverse("accounts:shared_gate")).content.decode()
    menu = _menu(body)
    assert body.count("data-theme-toggle") == 1 and "data-theme-toggle" in menu
    assert 'class="settingsmenu settingsmenu--themeonly"' in menu
    # Nothing else to offer somebody who has not signed in.
    assert SETTINGS_URL not in menu and reverse("haldus:index") not in menu
    assert ">Välju<" not in body


# -- what the menu holds ------------------------------------------------------------


def test_the_menu_holds_the_switch_notification_settings_and_haldus(settings):
    apply_admin_gate(settings)
    client = pass_gate(Client())
    choose(client, factories.UserFactory())
    menu = _menu(client.get(reverse("matters:my_work")).content.decode())

    assert '<summary class="settingsmenu__trigger" title="Seaded">' in menu
    assert '<span class="visually-hidden">Seaded</span>' in menu
    assert f'<a class="settingsmenu__item" href="{SETTINGS_URL}">Teavituste seaded</a>' in menu
    assert f'href="{reverse("haldus:index")}">Haldus</a>' in menu
    # The switch first, then the two places.
    assert menu.index("data-theme-toggle") < menu.index(SETTINGS_URL) < menu.index("/haldus/")
    assert "settingsmenu--themeonly" not in menu
    # The link leads somewhere that opens for this reader.
    assert client.get(SETTINGS_URL).status_code == 200


def test_a_reader_is_offered_the_notification_settings_and_no_new_teema(client, reader):
    """A READER reads every record (docs/adr/0181), so it has the bell and its settings."""
    client.force_login(reader)
    body = client.get(reverse("matters:my_work")).content.decode()
    assert SETTINGS_URL in _menu(body)
    assert "topbar__cta" not in _header(body)


def test_a_persona_less_gate_session_is_offered_neither_link(settings):
    """No persona: no bell, so no notification settings; no Haldus landing to show."""
    apply_admin_gate(settings)
    client = pass_gate(Client())
    body = client.get(reverse("accounts:choose_persona")).content.decode()
    menu = _menu(body)
    assert SETTINGS_URL not in menu
    assert reverse("haldus:index") not in menu
    assert "settingsmenu--themeonly" in menu
    assert "notifbell" not in _header(body)


def test_a_technical_administrator_is_not_offered_notification_settings(client, settings):
    """The bell is for internal readers; the menu offers its settings to the same people."""
    settings.AUTH_MODE = "none"
    client.force_login(factories.UserFactory(role="ADMINISTRATOR"))
    response = client.get(reverse("haldus:index"))
    menu = _menu(response.content.decode())
    assert SETTINGS_URL not in menu
    assert "notifbell" not in _header(response.content.decode())


def test_the_gear_marks_a_haldus_page_and_its_entry_is_current(settings):
    apply_admin_gate(settings)
    client = pass_gate(Client())
    choose(client, factories.UserFactory())
    menu = _menu(client.get(reverse("haldus:index")).content.decode())
    assert '<summary class="settingsmenu__trigger is-active"' in menu
    haldus = menu[menu.rindex("<a", 0, menu.index(">Haldus</a>")) :]
    assert 'aria-current="page"' in haldus and "is-active" in haldus
    assert menu.count('aria-current="page"') == 1


def test_the_menu_is_a_disclosure_that_works_without_script(client, specialist):
    """A real <details>: no `hidden`, no `onclick`, nothing a script must undo first."""
    client.force_login(specialist)
    menu = _menu(client.get(reverse("matters:my_work")).content.decode())
    assert menu.startswith('<details class="settingsmenu"')
    assert " hidden" not in menu and "onclick" not in menu
    assert "<summary" in menu
