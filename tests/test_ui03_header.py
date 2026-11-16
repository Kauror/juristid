"""The top bar's right-hand order, and the account menu (owner, 2026-11-16).

As rendered HTML in every sign-in mode:

* the right-hand controls read search → «+ Uus teema» → the bell → the account
  menu (whose work is on screen), and the account menu is the last thing on
  the bar — no separate settings gear and no «Välju» button beside it;
* clicking the name opens one menu whose own rows are Haldus and «Välju», in
  that order (components/account_menu_items.html; owner, 2026-11-16). In the
  shared gate they sit under the people to switch between, with no heading
  above them; elsewhere the avatar opens them;
* the light/dark switch and «Teavituste seaded» are not on the bar at all once
  signed in: both are on Haldus → Seaded, and «Teavituste seaded» is also the
  gear in the bell's panel. Before signing in the switch is the only thing
  behind the gear.

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
SIGN_OUT_URL = reverse("accounts:sign_out")


def _header(body: str) -> str:
    return body[body.index('<header class="topbar">') : body.index("</header>")]


def _rows(body: str) -> str:
    """The account menu's own rows (components/account_menu_items.html)."""
    header = _header(body)
    start = header.index('<div class="accountmenu"')
    return header[start : header.index("</form>", start) + len("</form>")]


def _account(body: str) -> str:
    """The account control as a whole: the persona switcher or the avatar menu."""
    header = _header(body)
    if 'class="personaswitch"' in header:
        return header[header.index('<div class="personaswitch"') :]
    start = header.index('<details class="settingsmenu"')
    return header[start : header.index("</details>", start) + len("</details>")]


def _assert_order(body: str, account_marker: str) -> None:
    header = _header(body)
    order = [
        ("search", 'class="searchfield"'),
        ("uus teema", "topbar__cta"),
        ("bell", "notifbell"),
        ("account", account_marker),
    ]
    positions = []
    for name, marker in order:
        assert header.count(marker) >= 1, f"{name} ({marker}) is not on the bar"
        positions.append((header.index(marker), name))
    assert [name for _, name in sorted(positions)] == [name for name, _ in order], positions
    # No gear and no «Välju» of their own on the bar: both live in the menu.
    assert 'title="Seaded"' not in header
    assert "topbar__signout" not in header
    assert header.count(f'action="{SIGN_OUT_URL}"') == 1
    assert SIGN_OUT_URL in _rows(body)
    # Nothing after the account control: it is the last thing on the bar.
    account = _account(body)
    tail = header[header.index(account) + len(account) :]
    assert re.sub(r"\s|</div>", "", tail) == "", tail


def _assert_rows(rows: str) -> None:
    """Haldus, then «Välju» — and neither the switch nor «Teavituste seaded»."""
    assert f'href="{reverse("haldus:index")}">Haldus</a>' in rows
    assert rows.index(">Haldus</a>") < rows.index(">Välju</button>")
    assert SETTINGS_URL not in rows
    assert "data-theme-toggle" not in rows


# -- the order, in each mode ------------------------------------------------------------


def test_the_order_under_the_shared_gate(settings):
    apply_admin_gate(settings)
    client = pass_gate(Client())
    choose(client, factories.UserFactory())
    body = client.get(reverse("matters:my_work")).content.decode()
    _assert_order(body, 'class="personaswitch"')


def test_the_order_under_personal_sign_in(client, settings):
    apply_local_password(settings)
    user, _ = person()
    sign_in(client, user)
    body = client.get(reverse("matters:my_work")).content.decode()
    _assert_order(body, 'class="settingsmenu"')
    # «Minu konto» leads the avatar's menu, above the shared rows.
    account = _account(body)
    assert account.index(reverse("accounts:profile")) < account.index('<div class="accountmenu"')


def test_the_order_when_signed_in_otherwise(client, specialist):
    client.force_login(specialist)
    body = client.get(reverse("matters:my_work")).content.decode()
    _assert_order(body, 'class="settingsmenu"')
    assert reverse("accounts:profile") not in _account(body)


# -- what the menu holds ------------------------------------------------------------


def test_the_persona_menu_holds_the_people_then_the_account_rows(settings):
    apply_admin_gate(settings)
    client = pass_gate(Client())
    choose(client, factories.UserFactory())
    body = client.get(reverse("matters:my_work")).content.decode()
    account = _account(body)
    _assert_rows(_rows(body))
    assert account.index("personaoption") < account.index('<div class="accountmenu"')
    # No heading above the people any more (owner, 2026-11-16).
    assert "personamenu__label" not in account
    assert ">Näita kelle tööd<" not in account


def test_the_avatar_menu_holds_the_account_rows(client, specialist):
    client.force_login(specialist)
    body = client.get(reverse("matters:my_work")).content.decode()
    _assert_rows(_rows(body))
    assert '<span class="avatar" aria-hidden="true">' in _account(body)


def test_there_is_no_sun_and_moon_button_on_the_bar_once_signed_in(client, specialist):
    client.force_login(specialist)
    body = client.get(reverse("matters:my_work")).content.decode()
    assert "data-theme-toggle" not in body
    assert SETTINGS_URL not in _header(body)


def test_haldus_seaded_holds_every_personal_setting(client, specialist):
    """Haldus → Seaded: the switch, «Teavituste seaded» and the blocks' reset."""
    client.force_login(specialist)
    body = client.get(reverse("haldus:index")).content.decode()
    start = body.index('aria-labelledby="seaded"')
    seaded = body[start : body.index("</section>", start)]
    assert body.count("data-theme-toggle") == 1 and "data-theme-toggle" in seaded
    assert "themetoggle--setting" in seaded
    assert f'href="{SETTINGS_URL}"' in seaded
    assert reverse("accounts:reset_disclosures") in seaded
    assert body.count(reverse("accounts:reset_disclosures")) == 1


def test_the_sign_in_page_keeps_only_the_theme_switch_behind_the_gear(settings):
    """Before signing in the gear holds the switch alone, and there is no «Välju»."""
    apply_admin_gate(settings)
    body = Client().get(reverse("accounts:shared_gate")).content.decode()
    header = _header(body)
    start = header.index('<details class="settingsmenu')
    menu = header[start : header.index("</details>", start)]
    assert body.count("data-theme-toggle") == 1 and "data-theme-toggle" in menu
    assert 'class="settingsmenu settingsmenu--themeonly"' in menu
    assert SETTINGS_URL not in menu and reverse("haldus:index") not in menu
    assert ">Välju<" not in body


def test_a_reader_is_offered_the_notification_settings_and_no_new_teema(client, reader):
    """A READER reads every record (docs/adr/0181), so it has the bell and its settings."""
    client.force_login(reader)
    body = client.get(reverse("matters:my_work")).content.decode()
    assert "notifbell" in _header(body)
    assert "topbar__cta" not in _header(body)
    assert SETTINGS_URL in client.get(reverse("haldus:index")).content.decode()
    assert SETTINGS_URL in client.get(reverse("notifications:panel")).content.decode()


def test_a_persona_less_gate_session_can_still_leave(settings):
    """No persona: no bell and no notification settings — but «Välju» is there."""
    apply_admin_gate(settings)
    client = pass_gate(Client())
    body = client.get(reverse("accounts:choose_persona")).content.decode()
    rows = _rows(body)
    assert SETTINGS_URL not in rows
    assert ">Välju</button>" in rows
    assert "notifbell" not in _header(body)


def test_a_technical_administrator_is_not_offered_notification_settings(client, settings):
    """The bell is for internal readers; the menu offers its settings to the same people."""
    settings.AUTH_MODE = "none"
    client.force_login(factories.UserFactory(role="ADMINISTRATOR"))
    response = client.get(reverse("haldus:index"))
    body = response.content.decode()
    assert SETTINGS_URL not in body
    assert "notifbell" not in _header(body)


def test_a_haldus_page_marks_the_trigger_and_its_entry_current(client, specialist):
    client.force_login(specialist)
    body = client.get(reverse("haldus:index")).content.decode()
    account = _account(body)
    assert '<summary class="settingsmenu__trigger accountmenu__trigger is-active"' in account
    rows = _rows(body)
    haldus = rows[rows.rindex("<a", 0, rows.index(">Haldus</a>")) :]
    assert 'aria-current="page"' in haldus and "is-active" in haldus
    assert rows.count('aria-current="page"') == 1


def test_the_avatar_menu_is_a_disclosure_that_works_without_script(client, specialist):
    """A real <details>: no `hidden`, no `onclick`, nothing a script must undo first."""
    client.force_login(specialist)
    account = _account(client.get(reverse("matters:my_work")).content.decode())
    assert account.startswith('<details class="settingsmenu"')
    assert " hidden" not in account and "onclick" not in account
    assert "<summary" in account
