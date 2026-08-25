"""Switching persona in a real browser, from the bar, without losing the page.

Runs against the second server — the one on `AUTH_MODE=shared_gate` — because
that is the only mode in which a persona exists to be switched. Everything
below is a property of the running system rather than of the DOM: the popover's
keyboard behaviour is script, the page it returns to is a server redirect, and
what the navigation offers afterwards is an authorization rule. None of those
can be asserted from a template.

No database access, on purpose. The seeded world's people are data in
`e2e/conftest.py`, so a browser test cannot mask an authorization bug by looking
the answer up around the UI (docs/adr/0010).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import navigation_targets, pass_the_gate

pytestmark = pytest.mark.e2e

#: Mirrors `app/core/management/commands/seed_e2e_data.py`. Three candidates:
#: two specialists and the department head. The administrator is seeded too and
#: must never appear, and the former colleague is inactive.
SPECIALIST = "Sandra"
HEAD = "Testosakonnajuht"
ADMINISTRATOR = "Testadministraator"

MY_WORK = "/minu-too/"
DEPARTMENT_WORK = "/osakonna-too/"
OVERVIEW = "/ulevaade/"


def _pill(page):
    return page.locator("#persona-pill")


def _menu(page):
    return page.locator("#persona-menu")


def _open_menu(page):
    pill = _pill(page)
    pill.click()
    expect(pill).to_have_attribute("aria-expanded", "true")
    expect(_menu(page)).to_be_visible()
    return _menu(page)


@pytest.fixture
def past_the_door(page, gate_base_url):
    """Behind the shared password, on Ülevaade, with nobody selected."""
    pass_the_gate(page, gate_base_url)
    page.goto(f"{gate_base_url}{OVERVIEW}")
    page.wait_for_load_state("networkidle")
    return page


# -- the flow the brief describes, end to end ------------------------------


def test_switching_from_the_bar_keeps_the_page_and_follows_the_role(past_the_door, gate_base_url):
    """The whole journey in one test, because the point is that it is one.

    Split into six, each would re-establish the same session and none would
    prove the thing that matters: that a person can move between colleagues'
    views from wherever they happen to be reading, and that what the
    application then offers them changes with the role they picked.
    """
    page = past_the_door

    # With nobody selected there is no personal queue to offer.
    assert MY_WORK not in navigation_targets(page)
    assert DEPARTMENT_WORK not in navigation_targets(page)

    # 3–5. Open the popover and walk to the first choice with the keyboard.
    menu = _open_menu(page)
    assert ADMINISTRATOR not in menu.inner_text(), (
        "a technical account is being offered as a persona"
    )
    page.keyboard.press("ArrowDown")
    focused = page.evaluate("() => document.activeElement.innerText")
    # Bounded. The choices wrap, so a name that never matches would otherwise
    # be a browser test that hangs until the job times out rather than one that
    # fails and says what it was looking for.
    for _ in range(8):
        if SPECIALIST in focused:
            break
        page.keyboard.press("ArrowDown")
        moved = page.evaluate("() => document.activeElement.innerText")
        assert moved != focused, "ArrowDown is not moving between the choices"
        focused = moved
    assert SPECIALIST in focused, f"never reached {SPECIALIST} with the arrow keys"
    page.keyboard.press("Enter")
    page.wait_for_load_state("networkidle")

    # 6–8. The popover is gone, the page is the one we were reading, and the
    # bar names who is now selected.
    assert page.url.endswith(OVERVIEW)
    expect(_menu(page)).to_be_hidden()
    expect(_pill(page)).to_have_attribute("aria-expanded", "false")
    expect(_pill(page)).to_contain_text(SPECIALIST)

    # A specialist has a personal queue and no department surface.
    assert MY_WORK in navigation_targets(page)
    assert DEPARTMENT_WORK not in navigation_targets(page)

    # 9–12. The department head, chosen with the mouse this time.
    menu = _open_menu(page)
    menu.get_by_role("button", name=HEAD, exact=False).click()
    page.wait_for_load_state("networkidle")

    assert page.url.endswith(OVERVIEW)
    expect(_pill(page)).to_contain_text(HEAD)
    assert DEPARTMENT_WORK in navigation_targets(page), (
        "the department head is not being offered the department surface"
    )

    # 13–15. Stepping back to nobody.
    menu = _open_menu(page)
    menu.get_by_role("button", name="Ilma kasutajata").click()
    page.wait_for_load_state("networkidle")

    assert page.url.endswith(OVERVIEW)
    assert MY_WORK not in navigation_targets(page)
    assert DEPARTMENT_WORK not in navigation_targets(page)
    expect(page.locator(".personapill--none")).to_be_visible()

    # 16–17. And it survives a reload: the choice is session state, not a
    # decoration the previous response happened to carry.
    page.reload()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".personapill--none")).to_be_visible()
    assert MY_WORK not in navigation_targets(page)


def test_the_selected_persona_survives_moving_between_pages(past_the_door, gate_base_url):
    page = past_the_door
    menu = _open_menu(page)
    menu.get_by_role("button", name=SPECIALIST, exact=False).click()
    page.wait_for_load_state("networkidle")

    page.goto(f"{gate_base_url}/teemad/")
    page.wait_for_load_state("networkidle")

    expect(_pill(page)).to_contain_text(SPECIALIST)


def test_switching_from_the_register_returns_to_the_register(past_the_door, gate_base_url):
    """The acceptance criterion the popover exists for (brief 19)."""
    page = past_the_door
    page.goto(f"{gate_base_url}/teemad/?olek=koik")
    page.wait_for_load_state("networkidle")

    menu = _open_menu(page)
    menu.get_by_role("button", name=SPECIALIST, exact=False).click()
    page.wait_for_load_state("networkidle")

    assert "/teemad/" in page.url
    assert "olek=koik" in page.url


# -- the popover is a keyboard control -------------------------------------


def test_the_pill_opens_from_the_keyboard(past_the_door):
    page = past_the_door
    _pill(page).focus()
    page.keyboard.press("Enter")

    expect(_menu(page)).to_be_visible()
    expect(_pill(page)).to_have_attribute("aria-expanded", "true")


def test_escape_closes_the_popover_and_gives_the_focus_back(past_the_door):
    """Closing without choosing must not strand the focus.

    A popover that shuts and leaves the focus on `<body>` costs a keyboard user
    a full tab through the bar to get back to where they were.
    """
    page = past_the_door
    _open_menu(page)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Escape")

    expect(_menu(page)).to_be_hidden()
    expect(_pill(page)).to_be_focused()


def test_arrow_keys_wrap_around_the_choices(past_the_door):
    page = past_the_door
    _open_menu(page)

    page.keyboard.press("ArrowUp")
    last = page.evaluate("() => document.activeElement.innerText")
    page.keyboard.press("ArrowDown")
    first = page.evaluate("() => document.activeElement.innerText")

    assert last and first and last != first


def test_a_click_outside_closes_the_popover(past_the_door):
    page = past_the_door
    _open_menu(page)

    # The footer: text, no links, and nowhere near the popover. Clicking a
    # heading would be a click on whatever the dashboard happens to put there.
    page.locator(".app__footer").click()

    expect(_menu(page)).to_be_hidden()
    expect(_pill(page)).to_have_attribute("aria-expanded", "false")


# -- the full page ----------------------------------------------------------


def test_the_full_page_lists_the_department_inside_the_normal_shell(past_the_door, gate_base_url):
    page = past_the_door
    page.goto(f"{gate_base_url}/konto/kasutaja/")
    page.wait_for_load_state("networkidle")

    expect(page.get_by_role("heading", name="Vali kasutaja")).to_be_visible()
    expect(page.locator(".topbar")).to_be_visible()
    expect(_pill(page)).to_be_visible()

    body = page.locator(".personabody").inner_text()
    assert SPECIALIST in body
    assert HEAD in body
    assert ADMINISTRATOR not in body
    assert "Ilma kasutajata" in body


def test_the_whole_row_is_the_button_on_the_full_page(past_the_door, gate_base_url):
    """Not a six-letter target on the far right of a 760px row (brief 12)."""
    page = past_the_door
    page.goto(f"{gate_base_url}/konto/kasutaja/")
    page.wait_for_load_state("networkidle")

    row = page.locator("button.personarow").first
    box = row.bounding_box()
    assert box["width"] > 400, "the row is not the target"
    assert box["height"] >= 52

    # Pressing the left end of the row — nowhere near the word "Vali" — selects.
    page.mouse.click(box["x"] + 20, box["y"] + box["height"] / 2)
    page.wait_for_load_state("networkidle")

    expect(page.locator(".personapill--none")).to_have_count(0)


def test_minu_too_without_a_persona_invents_nobody(past_the_door, gate_base_url):
    page = past_the_door
    page.goto(f"{gate_base_url}{MY_WORK}")
    page.wait_for_load_state("networkidle")

    assert "/konto/kasutaja/" in page.url
    expect(page.get_by_role("heading", name="Vali kasutaja")).to_be_visible()


def test_the_shell_does_not_scroll_sideways_at_any_supported_width(past_the_door, gate_base_url):
    """The popover is 308px wide and anchored to the right of the bar.

    Measured rather than looked at: a horizontal scrollbar is invisible in a
    screenshot and is exactly what an absolutely-positioned overlay produces
    when it is anchored past the edge of its container.
    """
    page = past_the_door
    page.goto(f"{gate_base_url}/konto/kasutaja/")

    for width in (1024, 1280, 1366, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_load_state("networkidle")
        _open_menu(page)
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1, f"the shell scrolls sideways at {width}px with the popover open"
        menu_right = page.evaluate(
            "() => document.getElementById('persona-menu').getBoundingClientRect().right"
        )
        assert menu_right <= width + 1, f"the popover hangs off the viewport at {width}px"
        page.keyboard.press("Escape")
