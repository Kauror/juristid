"""The theme switch, as a person uses it (docs/adr/0147).

Dark is the default; light is the alternative a browser chooses with the sun
and moon switch, and keeps. Where the switch is has moved more than once: a
button on the bar of its own until 2026-11-06, then a row of the gear
«Seaded» (UI-03), then briefly a row of the account menu behind the avatar.
Since 2026-11-16 (owner) the name menu holds only the people, Haldus and
«Välju», and signed in the switch is on one page only — Haldus → Seaded
(`/haldus/`, the first panel), drawn there as a button among the other
personal settings. Signed out it is still the only thing behind the gear on
the bar, because the sign-in page is where somebody meets the default again.

The helpers below follow that: on a signed-out page they open the gear, and
on a signed-in page without the Seaded switch they go to `/haldus/` first.
The theme itself is checked on whatever page a test is on — it belongs to the
document, not to the switch.

What these hold is the behaviour, not the palette — the palette's contrast is
tests/test_text_contrast.py and e2e/test_theme_contrast.py, and its appearance
is the `hele-*` scenarios of the visual suite:

* the default, the switch in both directions, its icon and its spoken name;
* signed in, the switch on Haldus → Seaded and nowhere else;
* the choice surviving a reload, a navigation, a new tab and an htmx swap;
* a light page never painting a dark frame first;
* blocked storage and a nonsense value in it;
* the signed-out pages, and what signing out does to the choice;
* a switch that writes nothing — no request at all — and loses nothing typed;
* the Seaded panel and its switch at every width, in both themes.

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


#: Signed in: the switch on Haldus → Seaded (templates/haldus/index.html).
SETTING = ".themetoggle--setting"

#: Signed out: the switch behind the gear, the only thing on the bar.
GEAR = ".topbar .settingsmenu [data-theme-toggle]"

#: The Seaded panel on Haldus.
SEADED = 'section[aria-labelledby="seaded"]'


def switch(page):
    """The switch on this page: the Seaded button if there is one, else the gear's."""
    if page.locator(SETTING).count():
        return page.locator(SETTING)
    return page.locator(GEAR)


def has_switch(page) -> bool:
    return page.locator(f"{SETTING}, {GEAR}").count() > 0


def open_gear(page) -> None:
    """Open the signed-out gear if it is not open already."""
    menu = page.locator(".topbar .settingsmenu")
    if not menu.evaluate("(details) => details.open"):
        page.locator(".topbar .settingsmenu__trigger").click()
    expect(menu).to_have_attribute("open", "")


def reach_switch(page) -> None:
    """Put the switch on screen.

    Signed out, that is opening the gear. Signed in, the switch exists only on
    Haldus → Seaded (2026-11-16), so a page without it is left for `/haldus/`
    on the same origin — the test server's or the gate server's alike.
    """
    if page.locator(SETTING).count():
        return
    if page.locator(GEAR).count():
        open_gear(page)
        return
    origin = page.evaluate("() => window.location.origin")
    page.goto(f"{origin}/haldus/")
    page.wait_for_load_state("networkidle")
    expect(page.locator(SETTING)).to_be_visible()


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
    """Press the switch — after going to Haldus → Seaded if signed in elsewhere."""
    reach_switch(page)
    switch(page).click()


def _assert_switch_offers(page, offered: str, other: str, shown: str, hidden: str) -> None:
    """Where the switch is on this page, its name and icon offer the other theme.

    A signed-in page other than Haldus has no switch at all; there only the
    document's theme is checked.
    """
    if not has_switch(page):
        return
    if not page.locator(SETTING).count():
        open_gear(page)
    expect(page.get_by_role("button", name=offered, exact=True)).to_be_visible()
    expect(page.get_by_role("button", name=other, exact=True)).to_have_count(0)
    expect(switch(page).locator(f".themetoggle__icon--{shown}")).to_be_visible()
    expect(switch(page).locator(f".themetoggle__icon--{hidden}")).to_be_hidden()


def assert_dark(page) -> None:
    assert theme(page) == "dark"
    assert canvas(page) == DARK_CANVAS
    _assert_switch_offers(page, TO_LIGHT, TO_DARK, "sun", "moon")


def assert_light(page) -> None:
    assert theme(page) == "light"
    assert canvas(page) == LIGHT_CANVAS
    _assert_switch_offers(page, TO_DARK, TO_LIGHT, "moon", "sun")


# ---------------------------------------------------------------------------
# The default and the switch
# ---------------------------------------------------------------------------


def test_dark_is_the_default_and_nothing_is_stored_until_somebody_chooses(page, base_url):
    sign_in(page, base_url, SANDRA)
    assert_dark(page)
    assert stored(page) is None
    assert page.evaluate("() => getComputedStyle(document.documentElement).colorScheme") == "dark"
    # And the switch, where it is, offers the light theme.
    reach_switch(page)
    assert_dark(page)
    assert stored(page) is None


def test_signed_in_the_switch_is_on_haldus_seaded_and_nowhere_else(page, base_url):
    """Not on the bar and not in the name menu: one button, in Haldus → Seaded.

    The owner moved the personal settings out of the name menu on 2026-11-16;
    the menu holds the people, Haldus and «Välju». A switch left behind in the
    menu or on the bar would be a second place to look for it.
    """
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")
    expect(page.locator("[data-theme-toggle]")).to_have_count(0)
    page.locator(".topbar .settingsmenu__trigger").click()
    expect(page.locator(".topbar .accountmenu")).to_be_visible()
    expect(page.locator(".topbar [data-theme-toggle]")).to_have_count(0)

    page.locator(".topbar").get_by_role("link", name="Haldus", exact=True).click()
    page.wait_for_url(f"{base_url}/haldus/")
    expect(page.locator("[data-theme-toggle]")).to_have_count(1)
    expect(page.locator(f"{SEADED} {SETTING}")).to_be_visible()
    expect(page.locator(SEADED).get_by_role("heading", name="Seaded", exact=True)).to_be_visible()
    # Seaded is the first panel on the page.
    expect(page.locator("main section.panel").first).to_have_attribute("aria-labelledby", "seaded")


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
    """Light changes every role on Minu asjad; dark restores every one exactly.

    The switch is on Haldus → Seaded (2026-11-16), so each press is made there
    and Minu asjad is read again afterwards — the same page, in the other theme.
    """
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")
    dark = colours(page)

    press_switch(page)
    assert_light(page)
    assert stored(page) == "light"
    assert page.evaluate("() => getComputedStyle(document.documentElement).colorScheme") == "light"
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")
    assert_light(page)
    light = colours(page)
    unchanged = {selector for selector in RECOLOURED if light[selector] == dark[selector]}
    assert not unchanged, f"the light theme left these as they were: {unchanged}"

    press_switch(page)
    assert_dark(page)
    assert stored(page) == "dark"
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")
    assert colours(page) == dark


def test_the_emblem_is_drawn_for_each_theme(page, base_url):
    """Inverted for the dark bar, as drawn on the white one — and drawn at all."""
    sign_in(page, base_url, SANDRA)
    reach_switch(page)
    logo = page.locator(".topbar__logo")
    assert logo.evaluate("(image) => image.complete && image.naturalWidth > 0")
    assert "invert" in logo.evaluate("(image) => getComputedStyle(image).filter")
    press_switch(page)
    assert logo.evaluate("(image) => getComputedStyle(image).filter") == "none"


def test_the_switch_works_from_the_keyboard_and_shows_its_focus(page, base_url):
    """On Haldus, Tab from the bar reaches the Seaded switch; Enter and Space press it.

    Until 2026-11-16 this tabbed into the account menu; the switch is the first
    control of the first panel on Haldus now, after the bar and Haldus's own
    sub-navigation.
    """
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/haldus/")
    page.wait_for_load_state("networkidle")
    page.locator("#global-search").focus()
    for _ in range(60):
        page.keyboard.press("Tab")
        if page.evaluate(f"() => document.activeElement.matches('{SETTING}')"):
            break
    else:
        raise AssertionError("Tab never reached the theme switch in Haldus → Seaded")

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
    # leaves behind is still light. (Until 2026-11-16 this also pressed the
    # switch on the swapped page; signed in, the switch is only on Haldus now,
    # which has no such swap.)
    page.fill("#teemad-otsing", "Konfidentsiaalne")
    page.wait_for_url("**q=Konfidentsiaalne**")
    wait_for_htmx(page)
    assert_light(page)

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

    `Clear-Site-Data: "cache", "storage"` on sign-out (ENG-009) is a promise
    that no trace of the session is left in this profile; the preference is not
    an exception to it. So the page signing out lands on is the dark default —
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
    reach_switch(page)

    sent: list[str] = []
    page.on("request", lambda request: sent.append(f"{request.method} {request.url}"))
    press_switch(page)
    press_switch(page)
    press_switch(page)
    page.wait_for_timeout(600)
    assert sent == []


def test_switching_keeps_what_was_typed(page, base_url):
    """A half-filled form survives the switch: it reloads nothing and submits nothing.

    Signed in, the switch is on Haldus → Seaded with no field beside it to
    lose (2026-11-16), so this is held where a form and the switch still share
    a page: the sign-in form under the signed-out gear. Until then it was text
    typed into «Uus teema» with the switch in the account menu; here it is a
    person chosen in the form that is not the default one (the first is
    pre-selected, so a reload would visibly undo the choice). Nothing is
    submitted.
    """
    page.goto(f"{base_url}/konto/arendus-sisselogimine/")
    page.wait_for_load_state("networkidle")
    choices = page.locator("input[name='user_id']")
    assert choices.count() > 1
    chosen = choices.last
    chosen.check()

    press_switch(page)
    assert_light(page)
    press_switch(page)
    assert_dark(page)

    expect(chosen).to_be_checked()
    expect(choices.first).not_to_be_checked()
    assert page.url.endswith("/konto/arendus-sisselogimine/")


# ---------------------------------------------------------------------------
# Haldus → Seaded at every width
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
def test_the_seaded_panel_and_its_switch_fit_every_width_in_both_themes(
    page, base_url, width, height
):
    """Haldus → Seaded, its switch and its other buttons on screen and clear of each other.

    Until UI-03 this measured the sun/moon button on the bar, then the gear,
    then the avatar whose menu held the switch. Signed in, the switch is not on
    the bar at all since 2026-11-16, so what can go wrong at a width is the
    Seaded panel: its row of buttons wraps, and none may leave the panel,
    overlap a neighbour or push the document sideways — in either theme, since
    the switch's name is longer in one than in the other. The signed-out gear
    at wide and narrow widths is e2e/test_ui03_header.py.
    """
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"{base_url}/haldus/")
    page.wait_for_load_state("networkidle")

    for expected in ("dark", "light"):
        assert theme(page) == expected
        panel = _box(page, SEADED)
        assert panel and panel["x"] >= 0 and panel["x"] + panel["width"] <= width + 0.5, panel
        controls = page.locator(
            f"{SEADED} .settingslist > a, {SEADED} .settingslist > button, "
            f"{SEADED} .settingslist__form button"
        )
        boxes = [
            box
            for index in range(controls.count())
            if (box := controls.nth(index).bounding_box()) is not None
        ]
        assert _box(page, SETTING) in boxes, "the switch is not among the Seaded controls"
        for index, box in enumerate(boxes):
            assert box["x"] >= panel["x"] - 0.5, box
            assert box["x"] + box["width"] <= panel["x"] + panel["width"] + 0.5, box
            for other in boxes[index + 1 :]:
                assert not _overlap(box, other), (box, other)
        assert not document_overflows(page), "the document scrolls sideways"
        press_switch(page)
