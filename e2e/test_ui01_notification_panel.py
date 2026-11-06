"""UI-01: the bell's notification panel in a browser.

`tests/test_ui01_notification_state.py` proves the state rules. This proves
what only a running page shows: the bell opens a panel under it without
navigating, opening marks nothing, the keyboard and an outside click open and
close it with focus returned to the bell, an item's `⋯` reads it and the
bell's number and the Minu asjad block follow without a reload, «Märgi kõik
loetuks» empties both, older items load a page at a time, and the panel fits a
phone.

The items are «Uus asi» hand-overs from Sandra to Martin, made through the
same service the Vastutaja control calls, so each test plants its own and
reads the bell's number relative to what was there before.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    MARTIN,
    REPOSITORY_ROOT,
    SANDRA,
    create_matter,
    open_add_panel,
    sign_in,
    unique_title,
)

pytestmark = pytest.mark.e2e

#: ``E2E_COUNT`` new Teemad, each handed by Sandra to Martin: one «Uus asi» each.
_PLANT_SCRIPT = "\n".join(
    [
        "import os",
        "from app.accounts.models import User",
        "from app.matters.services import assign_matter, create_matter",
        "martin = User.objects.get(upn='martin@example.invalid')",
        "sandra = User.objects.get(upn='sandra@example.invalid')",
        "for number in range(int(os.environ['E2E_COUNT'])):",
        "    title = f\"{os.environ['E2E_PREFIX']} {number:02}\"",
        "    matter = create_matter(title=title, actor=sandra, data_class='TEST')",
        "    assign_matter(matter=matter, owner=martin, actor=sandra)",
        "print('ok')",
    ]
)

BELL = "[data-notifbell]"
TRIGGER = "[data-notifbell-trigger]"
PANEL = "[data-notifpanel]"
BLOCK = "section[aria-label='Teavitused']"


def _plant(count: int, label: str) -> str:
    prefix = unique_title(label)
    result = subprocess.run(  # noqa: S603
        [sys.executable, "manage.py", "shell", "-c", _PLANT_SCRIPT],
        cwd=REPOSITORY_ROOT,
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "E2E_COUNT": str(count),
            "E2E_PREFIX": prefix,
        },
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, "\n".join(["stdout:", result.stdout, "stderr:", result.stderr])
    return prefix


def _count(page) -> int:
    badge = page.locator(f"{BELL} .notifbell__count")
    if badge.count() == 0:
        return 0
    return int(badge.inner_text())


def _open(page) -> None:
    page.locator(TRIGGER).click()
    expect(page.locator(PANEL)).to_be_visible()
    expect(page.locator(f"{PANEL} [data-notifpanel-body]")).to_be_attached()


def _item(page, title: str):
    return page.locator(f"{PANEL} [data-notif-item]", has_text=title)


def test_the_bell_opens_a_panel_that_marks_nothing_and_closes_back_to_the_bell(page, base_url):
    sign_in(page, base_url, MARTIN)
    prefix = _plant(2, "Paneeli teavitus")
    page.goto(f"{base_url}/minu-asjad/")
    expect(page.locator(f"{BELL} .notifbell__count")).to_be_visible()
    before = _count(page)
    trigger = page.locator(TRIGGER)
    expect(trigger).to_have_attribute("aria-expanded", "false")

    _open(page)

    assert page.url == f"{base_url}/minu-asjad/"
    expect(trigger).to_have_attribute("aria-expanded", "true")
    expect(page.locator(f"{PANEL} .notifpanel__title")).to_be_focused()
    item = _item(page, f"{prefix} 01")
    expect(item).to_have_attribute("data-notif-state", "unread")
    expect(page.locator(PANEL).get_by_role("button", name="Märgi kõik loetuks")).to_be_visible()
    expect(page.locator(PANEL).get_by_role("link", name="Kõik teavitused")).to_have_attribute(
        "href", "/teavitused/"
    )
    expect(page.locator(PANEL).get_by_role("link", name="Teavituste seaded")).to_be_visible()

    page.keyboard.press("Escape")
    expect(page.locator(PANEL)).to_be_hidden()
    expect(trigger).to_be_focused()
    expect(trigger).to_have_attribute("aria-expanded", "false")

    # Opening it marked nothing: the number is the same after a reload.
    page.reload()
    expect(page.locator(f"{BELL} .notifbell__count")).to_have_text(str(before))


def test_the_panel_works_from_the_keyboard_and_closes_on_an_outside_click(page, base_url):
    sign_in(page, base_url, MARTIN)
    prefix = _plant(1, "Klaviatuuri paneel")
    page.goto(f"{base_url}/minu-asjad/")
    trigger = page.locator(TRIGGER)
    trigger.focus()

    page.keyboard.press("Enter")
    expect(page.locator(PANEL)).to_be_visible()
    expect(page.locator(f"{PANEL} [data-notifpanel-body]")).to_be_attached()
    expect(page.locator(f"{PANEL} .notifpanel__title")).to_be_focused()
    page.keyboard.press("Tab")
    focused_inside = page.evaluate(
        "() => document.querySelector('[data-notifpanel]').contains(document.activeElement)"
    )
    assert focused_inside
    page.keyboard.press("Escape")
    expect(page.locator(PANEL)).to_be_hidden()
    expect(trigger).to_be_focused()

    page.keyboard.press("Space")
    expect(page.locator(PANEL)).to_be_visible()
    expect(_item(page, f"{prefix} 00")).to_be_visible()

    # An Escape inside an item's `⋯` closes only the menu.
    menu = _item(page, f"{prefix} 00").locator("summary.notifitem__menubutton")
    menu.click()
    expect(_item(page, f"{prefix} 00").get_by_role("button", name="Märgi loetuks")).to_be_visible()
    page.keyboard.press("Escape")
    expect(_item(page, f"{prefix} 00").get_by_role("button", name="Märgi loetuks")).to_be_hidden()
    expect(page.locator(PANEL)).to_be_visible()

    # A click outside closes it, and focus left on the page goes back to the bell.
    page.locator("h1").first.click()
    expect(page.locator(PANEL)).to_be_hidden()
    expect(trigger).to_be_focused()

    # Another menu announcing itself closes the panel too (the header contract).
    trigger.click()
    expect(page.locator(PANEL)).to_be_visible()
    page.evaluate(
        "() => document.dispatchEvent(new CustomEvent('juristid:menu-open',"
        " {detail: {id: 'settings-menu'}}))"
    )
    expect(page.locator(PANEL)).to_be_hidden()


def test_reading_an_item_updates_the_bell_and_the_block_without_a_reload(page, base_url):
    sign_in(page, base_url, MARTIN)
    prefix = _plant(1, "Loetav paneelis")
    title = f"{prefix} 00"
    page.goto(f"{base_url}/minu-asjad/")
    expect(page.locator(f"{BELL} .notifbell__count")).to_be_visible()
    before = _count(page)
    marker = page.evaluate("() => (window.__ui01 = Math.random())")

    _open(page)
    item = _item(page, title)
    item.locator("summary.notifitem__menubutton").click()
    item.get_by_role("button", name="Märgi loetuks").click()

    item = _item(page, title)
    expect(item).to_have_attribute("data-notif-state", "read")
    expect(item.locator("summary.notifitem__menubutton")).to_be_focused()
    expect(page.locator(f"{BELL} .notifbell__count")).to_have_text(str(before - 1))
    expect(page.locator(f"{BLOCK} [data-inbox-item]", has_text=title)).to_have_count(0)
    assert page.evaluate("() => window.__ui01") == marker, "the page reloaded"

    # And back: «Märgi lugemata» returns it to the bell.
    item.locator("summary.notifitem__menubutton").click()
    item.get_by_role("button", name="Märgi lugemata").click()
    expect(_item(page, title)).to_have_attribute("data-notif-state", "unread")
    expect(page.locator(f"{BELL} .notifbell__count")).to_have_text(str(before))


def test_mark_all_read_empties_the_bell_and_the_block_and_older_items_load(page, base_url):
    sign_in(page, base_url, MARTIN)
    prefix = _plant(17, "Kõik loetuks")
    page.goto(f"{base_url}/minu-asjad/")
    expect(page.locator(BLOCK)).to_be_visible()
    marker = page.evaluate("() => (window.__ui01 = Math.random())")

    _open(page)
    shown = page.locator(f"{PANEL} [data-notif-item]").count()
    page.locator(PANEL).get_by_role("button", name="Näita varasemaid").click()
    # The next page arrives under the first, which stays.
    expect(page.locator(f"{PANEL} [data-notif-item]").nth(shown)).to_be_attached()
    expect(_item(page, f"{prefix} 00")).to_be_attached()

    page.locator(PANEL).get_by_role("button", name="Märgi kõik loetuks").click()

    expect(page.locator(f"{PANEL} .notifpanel__status")).to_contain_text("Loetuks märgitud")
    expect(page.locator(f"{BELL} .notifbell__count")).to_have_count(0)
    expect(page.locator(BLOCK)).to_have_count(0)
    expect(page.locator(f"{PANEL} [data-notif-state='unread']")).to_have_count(0)
    # Read, not deleted: the history is still in the panel, muted.
    expect(page.locator(f"{PANEL} [data-notif-state='read']").first).to_be_visible()
    assert page.evaluate("() => window.__ui01") == marker, "the page reloaded"

    page.reload()
    expect(page.locator(f"{BELL} .notifbell__slot")).to_be_attached()
    expect(page.locator(f"{BELL} .notifbell__count")).to_have_count(0)


def test_the_panel_fits_a_phone(page, base_url):
    sign_in(page, base_url, MARTIN)
    _plant(1, "Telefoni paneel")
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base_url}/minu-asjad/")

    _open(page)

    box = page.locator(PANEL).bounding_box()
    assert box is not None
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    assert box["y"] + box["height"] <= 844
    no_scroll = page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    assert no_scroll
    page.keyboard.press("Escape")
    expect(page.locator(PANEL)).to_be_hidden()
    expect(page.locator(TRIGGER)).to_have_attribute("aria-expanded", "false")
    assert re.search(r"/minu-asjad/$", page.url)


def test_a_teema_save_lands_focus_where_it_did_after_the_panel_was_opened(page, base_url):
    """The closed panel keeps no landing of the application's (review F-1, QA-018).

    `static/js/app.js` focuses the first `data-focus-after-save` in the
    document after a swap that destroyed focus. The panel sits in the top bar,
    before `<main>`, so it must never carry one: after the panel has been
    opened and closed, a save on the Teema still leaves focus on «Lisa
    teemale», exactly as `e2e/test_correction_round_surfaces.py` measures it.
    """
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("Paneeli fookus"), owner=SANDRA)
    page.goto(url)
    _open(page)
    page.keyboard.press("Escape")
    expect(page.locator(PANEL)).to_be_hidden()
    expect(page.locator(f"{PANEL} [data-notifpanel-body]")).to_be_attached()

    open_add_panel(page, "marge-toovoit")
    page.fill("#id_victory_change", "Ministeerium lükkas jõustumise edasi")
    page.locator("#marge-toovoit button[type=submit]").click()
    page.wait_for_selector("text=Ministeerium lükkas jõustumise edasi", state="attached")

    focused = page.evaluate("() => document.activeElement && document.activeElement.id")
    assert focused == "lisa-teemale"
