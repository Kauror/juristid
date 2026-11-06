"""UI-03 in a browser: the right-hand order, «Seaded», and the bar at narrow widths.

What the rendered HTML says is held in tests/test_ui03_header.py. These are the
properties only a laid-out page has:

* the controls read search → «+ Uus teema» → persona → bell → settings →
  «Välju» left to right, with «Välju» at the bar's right edge;
* one height for every control and one gap between them;
* no overlap and no sideways scroll at 1024, 768 and 390 px;
* «Seaded» opens and closes the way a menu should — Escape, a click outside,
  the keyboard leaving it — gives the focus back to the gear, shows its focus,
  and takes part in `juristid:menu-open`, so no two top-bar menus are open at
  once.

Nothing here writes a record.
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, document_overflows, pass_the_gate, sign_in

pytestmark = pytest.mark.e2e

CONTROLS = [
    ("search", "#global-search"),
    ("uus teema", ".topbar__cta"),
    ("persona", ".topbar__account .personapill, .topbar__account .avatar"),
    ("bell", ".topbar__account .notifbell"),
    ("settings", ".settingsmenu__trigger"),
    ("välju", ".topbar__signout .button"),
]

#: The bar's control height (`--topbar-control-size`, tokens.css), in pixels.
CONTROL = 32


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


def _open_menu(page):
    page.locator(".settingsmenu__trigger").click()
    menu = page.locator(".settingsmenu")
    expect(menu).to_have_attribute("open", "")
    return menu


def _is_open(page, selector: str = ".settingsmenu") -> bool:
    return page.locator(selector).evaluate("(details) => details.open")


# -- order, size, widths --------------------------------------------------------------


@pytest.mark.parametrize("width", [1440, 1280, 1024])
def test_the_controls_read_in_the_owners_order_on_one_row(page, base_url, width):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")

    present = _present(page)
    names = [name for name, _ in present]
    assert names[:3] == ["search", "uus teema", "persona"] and names[-2:] == ["settings", "välju"]
    xs = [box["x"] for _, box in present]
    assert xs == sorted(xs), present

    bar = page.locator(".topbar").bounding_box()
    assert bar["height"] == 48
    signout = dict(present)["välju"]
    padding = page.locator(".topbar").evaluate(
        "(bar) => parseFloat(getComputedStyle(bar).paddingRight)"
    )
    assert abs(bar["x"] + bar["width"] - padding - (signout["x"] + signout["width"])) <= 1, (
        "«Välju» is not at the right edge"
    )


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
    # The same gap (`--topbar-gap`, 8px) between every neighbouring pair.
    gaps = [
        (f"{a} → {b}", round(box_b["x"] - (box_a["x"] + box_a["width"]), 1))
        for (a, box_a), (b, box_b) in pairwise(present)
    ]
    assert all(abs(gap - 8) <= 0.5 for _, gap in gaps), gaps


@pytest.mark.parametrize("width,height", [(1024, 768), (768, 1024), (390, 844)])
def test_no_overlap_and_no_sideways_scroll_on_narrow_screens(page, base_url, width, height):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")

    present = _present(page)
    assert {name for name, _ in present} >= {"search", "uus teema", "persona", "settings", "välju"}
    for index, (name, box) in enumerate(present):
        assert box["x"] >= 0 and box["x"] + box["width"] <= width + 0.5, (name, box)
        for other, other_box in present[index + 1 :]:
            assert not _overlap(box, other_box), f"{name} overlaps {other} at {width}px"
    assert not document_overflows(page), "the document scrolls sideways"

    # «Välju» is still the last control on its row, at the right.
    signout = dict(present)["välju"]
    same_row = [box for _, box in present if abs(box["y"] - signout["y"]) < 4]
    assert max(box["x"] for box in same_row) == signout["x"]

    _open_menu(page)
    panel = page.locator(".settingsmenu__panel").bounding_box()
    assert panel["x"] >= 0 and panel["x"] + panel["width"] <= width + 0.5, panel
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
        assert not document_overflows(page)


# -- the menu -------------------------------------------------------------------------


def test_escape_closes_the_menu_and_gives_the_focus_back_to_the_gear(page, base_url):
    sign_in(page, base_url, SANDRA)
    _open_menu(page)
    page.keyboard.press("Tab")  # onto the theme row
    page.keyboard.press("Escape")
    assert not _is_open(page)
    expect(page.locator(".settingsmenu__trigger")).to_be_focused()


def test_a_click_outside_closes_the_menu(page, base_url):
    sign_in(page, base_url, SANDRA)
    _open_menu(page)
    page.locator("main h1").first.click()
    assert not _is_open(page)
    expect(page.locator(".settingsmenu__trigger")).to_be_focused()


def test_a_click_into_the_search_closes_the_menu_and_keeps_the_focus_there(page, base_url):
    sign_in(page, base_url, SANDRA)
    _open_menu(page)
    page.locator("#global-search").click()
    assert not _is_open(page)
    expect(page.locator("#global-search")).to_be_focused()


def test_tabbing_out_of_the_menu_closes_it(page, base_url):
    sign_in(page, base_url, SANDRA)
    _open_menu(page)
    for _ in range(4):  # the switch, Teavituste seaded, Haldus, then «Välju»
        page.keyboard.press("Tab")
    expect(page.locator(".topbar__signout .button")).to_be_focused()
    assert not _is_open(page)


def test_the_theme_row_switches_without_closing_the_menu(page, base_url):
    sign_in(page, base_url, SANDRA)
    _open_menu(page)
    page.locator(".topbar [data-theme-toggle]").click()
    assert page.evaluate("() => document.documentElement.dataset.theme") == "light"
    assert _is_open(page)
    page.get_by_role("button", name="Lülita tumedale teemale", exact=True).click()
    assert page.evaluate("() => document.documentElement.dataset.theme") == "dark"


def test_the_menu_links_to_notification_settings_and_haldus(page, base_url):
    sign_in(page, base_url, SANDRA)
    _open_menu(page)
    page.locator(".settingsmenu").get_by_role("link", name="Teavituste seaded").click()
    page.wait_for_url(f"{base_url}/teavitused/seaded/")
    _open_menu(page)
    page.locator(".settingsmenu").get_by_role("link", name="Haldus", exact=True).click()
    page.wait_for_url(f"{base_url}/haldus/")


def test_the_gear_shows_its_keyboard_focus(page, base_url):
    sign_in(page, base_url, SANDRA)
    page.locator(".topbar__account").locator("a, button, summary").first.focus()
    for _ in range(4):
        if page.evaluate("() => document.activeElement.matches('.settingsmenu__trigger')"):
            break
        page.keyboard.press("Tab")
    outline = page.locator(".settingsmenu__trigger").evaluate(
        """(gear) => {
            const style = getComputedStyle(gear);
            return [style.outlineStyle, style.outlineWidth, gear.matches(':focus-visible')];
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


def test_veel_and_the_settings_menu_close_each_other(page, base_url):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")

    page.locator(".topnav__trigger").click()
    assert _is_open(page, ".topnav__more")
    _open_menu(page)
    page.wait_for_function("() => !document.querySelector('.topnav__more').open")

    page.locator(".topnav__trigger").click()
    page.wait_for_function("() => !document.querySelector('.settingsmenu').open")
    assert _is_open(page, ".topnav__more")


def test_the_persona_popover_and_the_settings_menu_close_each_other(page, gate_base_url):
    """Behind the shared gate, where the persona pill is a popover of its own."""
    pass_the_gate(page, gate_base_url)
    page.goto(f"{gate_base_url}/konto/kasutaja/")
    page.wait_for_load_state("networkidle")
    page.locator("button.personarow").filter(has_text="Sandra").first.click()
    page.wait_for_load_state("networkidle")

    pill = page.locator("[data-persona-trigger]")
    pill.click()
    expect(pill).to_have_attribute("aria-expanded", "true")
    _open_menu(page)
    expect(pill).to_have_attribute("aria-expanded", "false")
    expect(page.locator("#persona-menu")).to_be_hidden()

    pill.click()
    expect(pill).to_have_attribute("aria-expanded", "true")
    page.wait_for_function("() => !document.querySelector('.settingsmenu').open")
    # The persona stays one click away on the bar itself.
    expect(page.locator(".topbar__account [data-persona-trigger]")).to_be_visible()
