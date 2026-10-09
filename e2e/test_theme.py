"""The theme switch, as a person uses it (docs/adr/0147).

Dark is the default; light is the alternative a browser chooses with the moon
and sun on the bar, and keeps. What these hold is the behaviour, not the
palette — the palette's contrast is tests/test_text_contrast.py and
e2e/test_theme_contrast.py, and its appearance is the `hele-*` scenarios of the
visual suite:

* the default, the switch in both directions, its icon and its spoken name;
* the choice surviving a reload, a navigation, a new tab and an htmx swap;
* a light page never painting a dark frame first;
* blocked storage and a nonsense value in it;
* the signed-out pages, and what signing out does to the choice;
* a switch that writes nothing — no request at all — and loses nothing typed;
* the bar at every width, in both themes.

Nothing here files, edits or deletes a record: every test only reads the seeded
world, so it can share a shard with anything.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, document_overflows, sign_in, sign_out, wait_for_htmx

pytestmark = pytest.mark.e2e

KEY = "juristid-theme"
TO_LIGHT = "Lülita heledale teemale"
TO_DARK = "Lülita tumedale teemale"

#: Each theme's `--surface-base`, as the browser reports it back.
DARK_CANVAS = "rgb(16, 20, 24)"
LIGHT_CANVAS = "rgb(242, 244, 247)"

#: A blocked store, the way a locked-down profile or a privacy mode presents it:
#: touching `localStorage` at all throws.
BLOCKED_STORAGE = """
Object.defineProperty(window, "localStorage", {
  configurable: true,
  get() { throw new DOMException("Storage is blocked", "SecurityError"); },
});
"""


def theme(page) -> str | None:
    return page.evaluate("() => document.documentElement.getAttribute('data-theme')")


def stored(page) -> str | None:
    return page.evaluate(f"() => window.localStorage.getItem('{KEY}')")


def canvas(page) -> str:
    return page.evaluate("() => getComputedStyle(document.body).backgroundColor")


def switch(page):
    return page.locator(".topbar [data-theme-toggle]")


def preset(page, value: str) -> None:
    """A preference already in this browser before the first page loads.

    Written once, on the first document only, so a test that then changes it
    is not overwritten on its next navigation.
    """
    page.add_init_script(
        f"""
        try {{
          if (!sessionStorage.getItem("e2e-preset")) {{
            localStorage.setItem("{KEY}", {value!r});
            sessionStorage.setItem("e2e-preset", "1");
          }}
        }} catch (e) {{}}
        """
    )


def press_switch(page) -> None:
    switch(page).click()


def assert_dark(page) -> None:
    assert theme(page) == "dark"
    assert canvas(page) == DARK_CANVAS
    expect(page.get_by_role("button", name=TO_LIGHT, exact=True)).to_be_visible()
    expect(page.get_by_role("button", name=TO_DARK, exact=True)).to_have_count(0)
    expect(switch(page).locator(".themetoggle__icon--sun")).to_be_visible()
    expect(switch(page).locator(".themetoggle__icon--moon")).to_be_hidden()


def assert_light(page) -> None:
    assert theme(page) == "light"
    assert canvas(page) == LIGHT_CANVAS
    expect(page.get_by_role("button", name=TO_DARK, exact=True)).to_be_visible()
    expect(page.get_by_role("button", name=TO_LIGHT, exact=True)).to_have_count(0)
    expect(switch(page).locator(".themetoggle__icon--moon")).to_be_visible()
    expect(switch(page).locator(".themetoggle__icon--sun")).to_be_hidden()


# ---------------------------------------------------------------------------
# The default and the switch
# ---------------------------------------------------------------------------


def test_dark_is_the_default_and_nothing_is_stored_until_somebody_chooses(page, base_url):
    sign_in(page, base_url, SANDRA)
    assert_dark(page)
    assert stored(page) is None
    assert page.evaluate("() => getComputedStyle(document.documentElement).colorScheme") == "dark"


#: Elements on Minu asjad whose colours are all roles, read before and after.
RECOLOURED = {
    "body": "backgroundColor",
    ".topbar": "backgroundColor",
    ".topnav__link": "color",
    ".workrow2": "backgroundColor",
    ".workrow2__title, .workrow2 a": "color",
    "#global-search": "borderTopColor",
    ".app__footer": "color",
}


def colours(page) -> dict[str, str]:
    return {
        selector: page.locator(selector).first.evaluate(
            f"(element) => getComputedStyle(element).{property_}"
        )
        for selector, property_ in RECOLOURED.items()
    }


def test_the_switch_recolours_the_whole_page_and_puts_it_back(page, base_url):
    """Light changes every role on the page; dark restores every one exactly."""
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/minu-asjad/")
    dark = colours(page)

    press_switch(page)
    assert_light(page)
    assert stored(page) == "light"
    assert page.evaluate("() => getComputedStyle(document.documentElement).colorScheme") == "light"
    light = colours(page)
    unchanged = {selector for selector in RECOLOURED if light[selector] == dark[selector]}
    assert not unchanged, f"the light theme left these as they were: {unchanged}"

    press_switch(page)
    assert_dark(page)
    assert stored(page) == "dark"
    assert colours(page) == dark


def test_the_emblem_is_drawn_for_each_theme(page, base_url):
    """Inverted for the dark bar, as drawn on the white one — and drawn at all."""
    sign_in(page, base_url, SANDRA)
    logo = page.locator(".topbar__logo")
    assert logo.evaluate("(image) => image.complete && image.naturalWidth > 0")
    assert "invert" in logo.evaluate("(image) => getComputedStyle(image).filter")
    press_switch(page)
    assert logo.evaluate("(image) => getComputedStyle(image).filter") == "none"


def test_the_switch_works_from_the_keyboard_and_shows_its_focus(page, base_url):
    sign_in(page, base_url, SANDRA)
    page.locator("#global-search").focus()
    for _ in range(12):
        page.keyboard.press("Tab")
        if page.evaluate("() => document.activeElement.matches('[data-theme-toggle]')"):
            break
    else:
        raise AssertionError("Tab never reached the theme switch")

    outline = switch(page).evaluate(
        """(button) => {
            const style = getComputedStyle(button);
            return [style.outlineStyle, style.outlineWidth, button.matches(':focus-visible')];
        }"""
    )
    assert outline == ["solid", "2px", True], outline

    page.keyboard.press("Enter")
    assert_light(page)
    page.keyboard.press("Space")
    assert_dark(page)
    assert stored(page) == "dark"


# ---------------------------------------------------------------------------
# Keeping the choice
# ---------------------------------------------------------------------------


def test_the_choice_survives_a_reload_a_navigation_a_new_tab_and_an_htmx_swap(page, base_url):
    sign_in(page, base_url, SANDRA)
    press_switch(page)

    page.reload()
    assert_light(page)

    page.get_by_role("navigation", name="Peamine").get_by_role(
        "link", name="Teemad", exact=True
    ).click()
    page.wait_for_load_state("networkidle")
    assert_light(page)

    # The register's live search swaps the list in place (htmx). The page it
    # leaves behind is still light, and the switch on it is still bound.
    page.fill("#teemad-otsing", "Konfidentsiaalne")
    page.wait_for_url("**q=Konfidentsiaalne**")
    wait_for_htmx(page)
    assert_light(page)
    press_switch(page)
    assert_dark(page)
    press_switch(page)

    # Another tab of the same browser opens in the theme chosen in this one.
    other = page.context.new_page()
    other.goto(f"{base_url}/osakond/")
    other.wait_for_load_state("networkidle")
    assert_light(other)


def test_another_open_tab_follows_the_switch(page, base_url):
    """Two tabs of one browser never show two themes for long."""
    sign_in(page, base_url, SANDRA)
    other = page.context.new_page()
    other.goto(f"{base_url}/teemad/")
    other.wait_for_load_state("networkidle")

    press_switch(page)
    other.wait_for_function("() => document.documentElement.dataset.theme === 'light'")
    assert canvas(other) == LIGHT_CANVAS


def test_a_light_page_never_paints_a_dark_frame(page, base_url):
    """The theme is on <html> before there is a <body> to paint.

    Every animation frame from the moment the document exists records the
    theme and whether a body is there yet. A frame with no body has nothing of
    the page to paint; every frame with one must already be light. A deferred
    script — or the theme applied on `DOMContentLoaded` — fails this on the
    first frame that has a body, because that frame is the dark page.
    """
    sign_in(page, base_url, SANDRA)
    press_switch(page)
    page.add_init_script(
        """
        window.__frames = [];
        window.__themeAtBody = undefined;
        new MutationObserver((records, observer) => {
          if (document.body) {
            window.__themeAtBody = document.documentElement.getAttribute("data-theme");
            observer.disconnect();
          }
        }).observe(document, { childList: true, subtree: true });
        const sample = () => {
          window.__frames.push([
            document.documentElement && document.documentElement.getAttribute("data-theme"),
            Boolean(document.body),
          ]);
          if (document.readyState !== "complete" || window.__frames.length < 3) {
            requestAnimationFrame(sample);
          }
        };
        requestAnimationFrame(sample);
        """
    )
    for path in ("/minu-asjad/", "/teemad/", "/konto/arendus-sisselogimine/"):
        page.goto(f"{base_url}{path}")
        page.wait_for_load_state("load")
        page.wait_for_function("() => window.__frames.length >= 3")
        assert page.evaluate("() => window.__themeAtBody") == "light", path
        frames = page.evaluate("() => window.__frames")
        painted = [value for value, has_body in frames if has_body]
        assert painted and set(painted) == {"light"}, (path, frames)


# ---------------------------------------------------------------------------
# When the browser will not, or should not, keep it
# ---------------------------------------------------------------------------


def test_blocked_storage_leaves_a_working_dark_page_and_a_working_switch(page, base_url):
    """Nothing breaks: dark by default, switchable, and simply not remembered."""
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script(BLOCKED_STORAGE)

    sign_in(page, base_url, SANDRA)
    assert_dark(page)
    press_switch(page)
    assert_light(page)

    page.reload()
    assert_dark(page)
    assert not errors, errors


@pytest.mark.parametrize("value", ["sinine", "Light", ""])
def test_a_nonsense_value_in_storage_is_ignored(page, base_url, value):
    preset(page, value)
    page.goto(f"{base_url}/konto/arendus-sisselogimine/")
    assert_dark(page)
    press_switch(page)
    assert_light(page)
    assert stored(page) == "light"


def test_the_signed_out_pages_follow_the_choice_and_carry_the_switch(page, base_url):
    """Before anybody has signed in: the sign-in page is light, and switchable."""
    preset(page, "light")
    page.goto(f"{base_url}/konto/arendus-sisselogimine/")
    page.wait_for_load_state("networkidle")
    assert_light(page)
    press_switch(page)
    assert_dark(page)
    assert stored(page) == "dark"


def test_signing_out_clears_the_choice_with_everything_else_and_signing_in_keeps_a_new_one(
    page, base_url
):
    """The preference is the browser's, and signing out empties the browser.

    `Clear-Site-Data: "storage"` on sign-out (ENG-009) is a promise that no
    trace of the session is left in this profile; the preference is not an
    exception to it. So the page signing out lands on is the dark default —
    with the switch on it, and a choice made there carries into the session.
    """
    sign_in(page, base_url, SANDRA)
    press_switch(page)
    assert_light(page)

    sign_out(page, base_url)
    assert_dark(page)
    assert stored(page) is None

    page.goto(f"{base_url}/konto/arendus-sisselogimine/")
    press_switch(page)
    sign_in(page, base_url, SANDRA)
    assert_light(page)


# ---------------------------------------------------------------------------
# What the switch does not do
# ---------------------------------------------------------------------------


def test_switching_sends_nothing_to_the_server(page, base_url):
    """No request of any kind: no save, no preference row, no audit event."""
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/")
    page.wait_for_load_state("networkidle")

    sent: list[str] = []
    page.on("request", lambda request: sent.append(f"{request.method} {request.url}"))
    press_switch(page)
    press_switch(page)
    press_switch(page)
    page.wait_for_timeout(600)
    assert sent == []


def test_switching_keeps_what_was_typed(page, base_url):
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    title = page.get_by_role("textbox", name="Pealkiri")
    title.fill("Teema, mis jääb alles")
    page.locator("#global-search").fill("otsing jääb ka")

    press_switch(page)
    assert_light(page)
    press_switch(page)
    assert_dark(page)

    expect(title).to_have_value("Teema, mis jääb alles")
    expect(page.locator("#global-search")).to_have_value("otsing jääb ka")
    assert page.url.endswith("/teemad/uus/")


# ---------------------------------------------------------------------------
# The bar at every width
# ---------------------------------------------------------------------------

WIDTHS = [(375, 812), (768, 1024), (1024, 768), (1440, 900), (1920, 1080)]


def _box(page, selector: str):
    element = page.locator(selector).first
    return element.bounding_box() if element.count() and element.is_visible() else None


def _overlap(a, b) -> bool:
    return not (
        a["x"] + a["width"] <= b["x"]
        or b["x"] + b["width"] <= a["x"]
        or a["y"] + a["height"] <= b["y"]
        or b["y"] + b["height"] <= a["y"]
    )


@pytest.mark.parametrize("width,height", WIDTHS, ids=lambda value: str(value))
def test_the_switch_fits_the_bar_at_every_width_in_both_themes(page, base_url, width, height):
    """On screen, clear of its neighbours, and costing the bar nothing.

    At 861px and up the bar is one 48px row and must stay one; below that it is
    a deliberate two-row layout, and the switch may not add a third.
    """
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")
    bar_height = page.locator(".topbar").bounding_box()["height"]

    for expected in ("dark", "light"):
        assert theme(page) == expected
        toggle = _box(page, ".topbar [data-theme-toggle]")
        assert toggle, "the switch is not on the bar"
        assert toggle["x"] >= 0 and toggle["x"] + toggle["width"] <= width
        for neighbour in (
            ".personapill",
            ".avatar",
            ".topbar__signout",
            ".searchfield",
            ".topbar__cta",
            ".topbar__env",
        ):
            other = _box(page, neighbour)
            assert not (other and _overlap(toggle, other)), f"the switch overlaps {neighbour}"
        assert not document_overflows(page), "the document scrolls sideways"
        assert page.locator(".topbar").bounding_box()["height"] == bar_height
        if width > 860:
            assert bar_height == 48
        press_switch(page)
