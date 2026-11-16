"""The top bar in a browser: the right-hand order, the account menu, narrow widths.

What the rendered HTML says is held in tests/test_ui03_header.py. These are the
properties only a laid-out page has:

* the controls read search → «+ Uus teema» → (a gap) → bell → the account
  menu left to right, with the account menu at the bar's right edge; there is
  no settings gear and no «Välju» on the bar itself (owner, 2026-11-16);
* the account menu's own rows are Haldus and «Välju», «Välju» last; since
  2026-11-16 (owner) neither the theme switch nor «Teavituste seaded» is in it
  — both are on Haldus → Seaded, and «Teavituste seaded» is also the gear in
  the bell's panel — and the gate's popover has no heading above the people;
* one height for every control, one gap between neighbours and one wider gap
  before the personal controls;
* no overlap and no sideways scroll at 1024, 768 and 390 px;
* the menu opens and closes the way a menu should — Escape, a click outside,
  the keyboard leaving it — gives the focus back to its trigger, shows its
  focus, and takes part in `juristid:menu-open`, so no two top-bar menus are
  open at once.

Nothing here writes a record.
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, document_overflows, pass_the_gate, sign_in

pytestmark = pytest.mark.e2e

#: The account menu's trigger: the avatar outside the shared gate (the default
#: e2e server), the persona pill in it.
ACCOUNT = ".topbar__account .settingsmenu__trigger, .topbar__account .personapill"

CONTROLS = [
    ("search", "#global-search"),
    ("uus teema", ".topbar__cta"),
    ("bell", ".topbar__account .notifbell"),
    ("account", ACCOUNT),
]

#: The bar's control height (`--topbar-control-size`, tokens.css), in pixels.
CONTROL = 32

#: The account menu's rows, top to bottom (components/account_menu_items.html).
ROWS = (
    "() => Array.from(document.querySelectorAll("
    "'.topbar .accountmenu a, .topbar .accountmenu button'"
    ")).map((row) => row.innerText.trim())"
)


def _box(page, selector: str):
    element = page.locator(selector).first
    if not element.count() or not element.is_visible():
        return None
    return element.bounding_box()


def _present(page) -> list[tuple[str, dict]]:
    return [(name, box) for name, selector in CONTROLS if (box := _box(page, selector))]


def _overlap(a, b) -> bool:
    return not (
        a["x"] + a["width"] <= b["x"]
        or b["x"] + b["width"] <= a["x"]
        or a["y"] + a["height"] <= b["y"]
        or b["y"] + b["height"] <= a["y"]
    )


def _trigger(page):
    return page.locator(".topbar .settingsmenu__trigger")


def _open_menu(page):
    _trigger(page).click()
    menu = page.locator(".topbar .settingsmenu")
    expect(menu).to_have_attribute("open", "")
    return menu


def _is_open(page, selector: str = ".topbar .settingsmenu") -> bool:
    return page.locator(selector).evaluate("(details) => details.open")


def _assert_no_gear_and_no_signout_on_the_bar(page) -> None:
    expect(page.locator(".topbar__signout")).to_have_count(0)
    expect(page.locator('.topbar summary[title="Seaded"]')).to_have_count(0)
    # «Välju» exists only inside the (closed) account menu.
    expect(page.locator(".topbar").get_by_role("button", name="Välju")).to_be_hidden()


# -- order, size, widths --------------------------------------------------------------


@pytest.mark.parametrize("width", [1440, 1280, 1024])
def test_the_controls_read_in_the_owners_order_on_one_row(page, base_url, width):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")

    _assert_no_gear_and_no_signout_on_the_bar(page)
    present = _present(page)
    names = [name for name, _ in present]
    assert names == ["search", "uus teema", "bell", "account"], names
    xs = [box["x"] for _, box in present]
    assert xs == sorted(xs), present

    bar = page.locator(".topbar").bounding_box()
    assert bar["height"] == 48
    account = dict(present)["account"]
    padding = page.locator(".topbar").evaluate(
        "(bar) => parseFloat(getComputedStyle(bar).paddingRight)"
    )
    assert abs(bar["x"] + bar["width"] - padding - (account["x"] + account["width"])) <= 1, (
        "the account menu is not at the right edge"
    )
    # The avatar names whose account it is, not «Seaded».
    expect(_trigger(page)).to_have_attribute("title", SANDRA.display_name)


@pytest.mark.parametrize("width", [1440, 1024])
def test_one_height_and_one_gap_for_every_control(page, base_url, width):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")

    present = _present(page)
    bar = page.locator(".topbar").bounding_box()
    centre = bar["y"] + bar["height"] / 2
    for name, box in present:
        if name == "bell":
            continue  # the notification panel's own box (UI-01), sized from the same token
        assert abs(box["height"] - CONTROL) <= 0.5, (name, box["height"])
        assert abs(box["y"] + box["height"] / 2 - centre) <= 1, (name, "not centred")
    # The same gap (`--topbar-gap`, 8px) between every neighbouring pair, and
    # the personal controls set apart from the work controls by the account
    # group's own margin (owner, 2026-11-16).
    apart = page.locator(".topbar__account").evaluate(
        "(group) => parseFloat(getComputedStyle(group).marginLeft)"
    )
    assert apart > 0, "the personal controls are not set apart"
    gaps = [
        (a, b, round(box_b["x"] - (box_a["x"] + box_a["width"]), 1))
        for (a, box_a), (b, box_b) in pairwise(present)
    ]
    for a, b, gap in gaps:
        expected = 8 + apart if (a, b) == ("uus teema", "bell") else 8
        assert abs(gap - expected) <= 0.5, gaps


@pytest.mark.parametrize("width,height", [(1024, 768), (768, 1024), (390, 844)])
def test_no_overlap_and_no_sideways_scroll_on_narrow_screens(page, base_url, width, height):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")

    present = _present(page)
    assert {name for name, _ in present} >= {"search", "uus teema", "account"}
    for index, (name, box) in enumerate(present):
        assert box["x"] >= 0 and box["x"] + box["width"] <= width + 0.5, (name, box)
        for other, other_box in present[index + 1 :]:
            assert not _overlap(box, other_box), f"{name} overlaps {other} at {width}px"
    assert not document_overflows(page), "the document scrolls sideways"

    # The account menu is still the last control on its row, at the right.
    account = dict(present)["account"]
    same_row = [box for _, box in present if abs(box["y"] - account["y"]) < 4]
    assert max(box["x"] for box in same_row) == account["x"]

    _open_menu(page)
    panel = page.locator(".topbar .settingsmenu__panel").bounding_box()
    assert panel["x"] >= 0 and panel["x"] + panel["width"] <= width + 0.5, panel
    signout = page.locator(".topbar .accountmenu").get_by_role("button", name="Välju")
    expect(signout).to_be_visible()
    box = signout.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= width + 0.5, box
    assert not document_overflows(page)


def test_the_signed_out_bar_has_the_gear_alone_at_its_right_edge(page, base_url):
    for width in (1440, 390):
        page.set_viewport_size({"width": width, "height": 800})
        page.goto(f"{base_url}/konto/arendus-sisselogimine/")
        page.wait_for_load_state("networkidle")
        expect(page.locator(".topbar [data-theme-toggle]")).to_be_hidden()
        gear = page.locator(".settingsmenu__trigger").bounding_box()
        bar = page.locator(".topbar").bounding_box()
        assert bar["x"] + bar["width"] - (gear["x"] + gear["width"]) <= 24, (width, gear)
        _open_menu(page)
        expect(page.locator(".topbar [data-theme-toggle]")).to_be_visible()
        # Nobody to sign out before signing in.
        expect(page.locator(".topbar").get_by_role("button", name="Välju")).to_have_count(0)
        assert not document_overflows(page)


# -- the menu -------------------------------------------------------------------------


def test_the_account_menu_holds_its_rows_in_the_owners_order(page, base_url):
    """Haldus, then «Välju» last — no theme switch and no «Teavituste seaded» (2026-11-16)."""
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")
    _open_menu(page)
    signout = page.locator(".topbar .accountmenu").get_by_role("button", name="Välju")
    expect(signout).to_be_visible()

    rows = page.evaluate(ROWS)
    assert rows == ["Haldus", "Välju"], rows
    expect(page.locator(".topbar [data-theme-toggle]")).to_have_count(0)
    expect(page.locator(".topbar .settingsmenu").get_by_text("Teavituste seaded")).to_have_count(0)
    # And «Välju» is drawn last, under Haldus.
    haldus_box = (
        page.locator(".topbar .accountmenu")
        .get_by_role("link", name="Haldus", exact=True)
        .bounding_box()
    )
    assert signout.bounding_box()["y"] > haldus_box["y"]


def test_escape_closes_the_menu_and_gives_the_focus_back_to_the_trigger(page, base_url):
    sign_in(page, base_url, SANDRA)
    _open_menu(page)
    page.keyboard.press("Tab")  # onto the first row
    page.keyboard.press("Escape")
    assert not _is_open(page)
    expect(_trigger(page)).to_be_focused()


def test_a_click_outside_closes_the_menu(page, base_url):
    sign_in(page, base_url, SANDRA)
    _open_menu(page)
    page.locator("main h1").first.click()
    assert not _is_open(page)
    expect(_trigger(page)).to_be_focused()


def test_a_click_into_the_search_closes_the_menu_and_keeps_the_focus_there(page, base_url):
    sign_in(page, base_url, SANDRA)
    _open_menu(page)
    page.locator("#global-search").click()
    assert not _is_open(page)
    expect(page.locator("#global-search")).to_be_focused()


def test_tabbing_out_of_the_menu_closes_it(page, base_url):
    sign_in(page, base_url, SANDRA)
    _open_menu(page)
    for _ in range(2):  # Haldus, then «Välju» (2026-11-16)
        page.keyboard.press("Tab")
    signout = page.locator(".topbar .accountmenu").get_by_role("button", name="Välju")
    expect(signout).to_be_focused()
    assert _is_open(page)
    page.keyboard.press("Tab")  # past the last row, out of the menu
    page.wait_for_function("() => !document.querySelector('.topbar .settingsmenu').open")
    assert page.evaluate(
        "() => !document.querySelector('.topbar .settingsmenu').contains(document.activeElement)"
    ), "the focus stayed inside the closed menu"


def test_the_theme_switch_left_the_menu_for_haldus_seaded(page, base_url):
    """The menu's Haldus row leads to the switch; pressing it there changes the theme.

    Until 2026-11-16 the switch was a row of this menu and this test pressed it
    without the menu closing. The row is gone (owner): signed in, the switch
    is the first thing in Haldus → Seaded, one click from the menu.
    """
    sign_in(page, base_url, SANDRA)
    _open_menu(page)
    expect(page.locator(".topbar [data-theme-toggle]")).to_have_count(0)
    page.locator(".topbar .accountmenu").get_by_role("link", name="Haldus", exact=True).click()
    page.wait_for_url(f"{base_url}/haldus/")
    seaded = page.locator('section[aria-labelledby="seaded"]')
    seaded.get_by_role("button", name="Lülita heledale teemale", exact=True).click()
    assert page.evaluate("() => document.documentElement.dataset.theme") == "light"
    seaded.get_by_role("button", name="Lülita tumedale teemale", exact=True).click()
    assert page.evaluate("() => document.documentElement.dataset.theme") == "dark"
    assert page.url == f"{base_url}/haldus/"


def test_the_menu_links_to_haldus_and_notification_settings_are_elsewhere(page, base_url):
    """Haldus from the menu; «Teavituste seaded» from the bell's gear and from Seaded.

    «Teavituste seaded» left the menu on 2026-11-16; the two ways to it that
    remain are the gear icon in the bell's panel header and the button in
    Haldus → Seaded.
    """
    sign_in(page, base_url, SANDRA)
    page.locator("[data-notifbell-trigger]").click()
    expect(page.locator("[data-notifpanel]")).to_be_visible()
    page.locator("[data-notifpanel] a.notifpanel__settings").click()
    page.wait_for_url(f"{base_url}/teavitused/seaded/")

    _open_menu(page)
    page.locator(".topbar .accountmenu").get_by_role("link", name="Haldus", exact=True).click()
    page.wait_for_url(f"{base_url}/haldus/")
    page.locator('section[aria-labelledby="seaded"]').get_by_role(
        "link", name="Teavituste seaded"
    ).click()
    page.wait_for_url(f"{base_url}/teavitused/seaded/")


def test_the_trigger_shows_its_keyboard_focus(page, base_url):
    sign_in(page, base_url, SANDRA)
    page.locator(".topbar__account").locator("a, button, summary").first.focus()
    for _ in range(4):
        if page.evaluate("() => document.activeElement.matches('.settingsmenu__trigger')"):
            break
        page.keyboard.press("Tab")
    outline = _trigger(page).evaluate(
        """(trigger) => {
            const style = getComputedStyle(trigger);
            return [style.outlineStyle, style.outlineWidth, trigger.matches(':focus-visible')];
        }"""
    )
    assert outline == ["solid", "2px", True], outline


# -- one menu at a time ---------------------------------------------------------------


def test_opening_the_menu_announces_it_and_another_menu_closes_it(page, base_url):
    """The shared `juristid:menu-open` contract with the notification panel (UI-01)."""
    sign_in(page, base_url, SANDRA)
    page.evaluate(
        """() => {
            window.__opened = [];
            document.addEventListener("juristid:menu-open",
                (event) => window.__opened.push(event.detail.id));
        }"""
    )
    _open_menu(page)
    page.wait_for_function("() => window.__opened.includes('settings-menu')")

    # The notification panel (or any other menu) opening closes it, without
    # taking the focus anywhere.
    page.evaluate(
        """() => document.dispatchEvent(new CustomEvent("juristid:menu-open",
            {detail: {id: "notification-panel"}}))"""
    )
    assert not _is_open(page)

    # Its own announcement does not close it.
    _open_menu(page)
    page.evaluate(
        """() => document.dispatchEvent(new CustomEvent("juristid:menu-open",
            {detail: {id: "settings-menu"}}))"""
    )
    assert _is_open(page)


def test_behind_the_gate_the_persona_popover_is_the_account_menu(page, gate_base_url):
    """Behind the shared gate the persona pill opens the one menu, «Välju» at its foot.

    No separate gear or «Välju» on the bar; the account rows follow the people
    to switch between, and another menu opening still closes the popover.
    """
    pass_the_gate(page, gate_base_url)
    page.goto(f"{gate_base_url}/konto/kasutaja/")
    page.wait_for_load_state("networkidle")
    page.locator("button.personarow").filter(has_text="Sandra").first.click()
    page.wait_for_load_state("networkidle")

    _assert_no_gear_and_no_signout_on_the_bar(page)
    expect(page.locator(".topbar .settingsmenu")).to_have_count(0)
    # The pill is the last control on the bar.
    pill = page.locator("[data-persona-trigger]")
    pill_box = pill.bounding_box()
    for name, box in _present(page):
        if name != "account":
            assert box["x"] + box["width"] <= pill_box["x"] + 0.5, f"{name} is right of the pill"

    pill.click()
    expect(pill).to_have_attribute("aria-expanded", "true")
    menu = page.locator("#persona-menu")
    signout = menu.locator(".accountmenu").get_by_role("button", name="Välju")
    expect(signout).to_be_visible()
    # No theme switch, no «Teavituste seaded» and no «Näita kelle tööd»
    # heading in the popover any more (owner, 2026-11-16).
    expect(menu.locator("[data-theme-toggle]")).to_have_count(0)
    expect(menu.locator(".personamenu__label")).to_have_count(0)
    expect(menu.get_by_text("Näita kelle tööd")).to_have_count(0)
    expect(menu.get_by_text("Teavituste seaded")).to_have_count(0)
    rows = page.evaluate(ROWS)
    assert rows[-1] == "Välju", rows
    assert not any(row.startswith("Lülita ") for row in rows), rows
    # The account rows come after the people.
    people = menu.locator(".personaoption").last.bounding_box()
    assert signout.bounding_box()["y"] > people["y"]

    # Another top-bar menu opening closes the popover (header-menu.js).
    page.evaluate(
        """() => document.dispatchEvent(new CustomEvent("juristid:menu-open",
            {detail: {id: "notification-panel"}}))"""
    )
    expect(pill).to_have_attribute("aria-expanded", "false")
    expect(menu).to_be_hidden()
    # The persona stays one click away on the bar itself.
    expect(page.locator(".topbar__account [data-persona-trigger]")).to_be_visible()
