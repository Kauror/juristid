"""The 2026-08-27 UX pass, driven the way a person drives it.

`tests/test_ux_pass.py` proves what the numbers mean and what the routes do.
This file proves the parts that only exist in a browser: that a keystroke moves
the selection, that a chip fills the field it claims to, that a disclosure opens
and hands over focus, and that eight viewport widths do not put a horizontal
scrollbar on the page.

Every shortcut asserted here also has a visible control asserted beside it. A
keyboard-only affordance is a feature half the department cannot use
(AGENTS.md, UX quality).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import (
    OPEN_TITLE,
    UNASSIGNED_TITLE,
)
from e2e.conftest import HEAD, SANDRA, sign_in

pytestmark = pytest.mark.e2e

#: The widths the responsive pass covers. Two laptop classes, the docked
#: 1280/1024 pair, a small window, and the two phone widths a lawyer opens a
#: link on from a train.
WIDTHS = (1440, 1366, 1280, 1024, 900, 720, 480, 375)

PAGES = ("/osakond/", "/minu-asjad/", "/teemad/")


def overflows(page) -> bool:
    return page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )


def open_matter(page, base_url: str, title: str) -> None:
    page.goto(f"{base_url}/teemad/?olek=koik&q={title.split()[0]}")
    page.wait_for_load_state("networkidle")
    page.get_by_role("link", name=title, exact=False).first.click()
    page.wait_for_load_state("networkidle")


# =========================================================================
# 1d — the composer
# =========================================================================


def test_the_composer_opens_with_l_and_never_while_somebody_is_typing(page, base_url):
    """`L` is a shortcut, not the only way in: the closed row is its own
    <summary> and opens on a click (design handoff 1d)."""
    sign_in(page, base_url, SANDRA)
    open_matter(page, base_url, OPEN_TITLE)

    composer = page.locator("details.uxcomp")
    expect(composer).to_have_count(1)
    assert composer.evaluate("node => node.open") is False

    page.keyboard.press("l")
    assert composer.evaluate("node => node.open") is True
    expect(page.locator("textarea.composer__body")).to_be_focused()

    # And the same key inside the box types a letter rather than doing anything.
    page.keyboard.type("l")
    assert page.locator("textarea.composer__body").input_value().endswith("l")


def test_a_quick_date_fills_the_field_that_is_actually_submitted(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_matter(page, base_url, OPEN_TITLE)

    page.locator("summary.uxcomp__collapsed").click()
    chip = page.locator("[data-quickdate]").filter(has_text="+1 nädal").first
    expected = chip.get_attribute("data-quickdate")
    chip.click()

    page.locator("details.uxcomp__date > summary").click()
    expect(page.locator("#id_next_date")).to_have_value(expected)
    # The chip now says the day it means, not just the span.
    expect(chip).to_contain_text("→")
    assert "is-selected" in (chip.get_attribute("class") or "")


def test_the_composer_still_saves_with_ctrl_enter(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_matter(page, base_url, OPEN_TITLE)

    page.keyboard.press("l")
    page.locator("textarea.composer__body").fill("Sünteetiline kiirsissekanne klaviatuurilt.")
    page.keyboard.press("Control+Enter")
    page.wait_for_load_state("networkidle")

    expect(page.locator("#teema-vaade")).to_contain_text("Sünteetiline kiirsissekanne")


def test_every_advanced_composer_field_is_still_reachable(page, base_url):
    """Folding the composer dropped nothing. The precision control, the stored
    date meaning and the closure block are all one disclosure away."""
    sign_in(page, base_url, SANDRA)
    open_matter(page, base_url, OPEN_TITLE)

    page.locator("summary.uxcomp__collapsed").click()
    page.locator("details.uxcomp__more > summary").click()
    expect(page.locator("#id_next_date_semantics")).to_be_visible()
    expect(page.locator("input[name=next_precision]").first).to_be_visible()

    page.get_by_role("button", name="+ Lõpeta teema").click()
    expect(page.locator("#id_close_matter")).to_be_visible()


# =========================================================================
# 1c — Järgmiseks
# =========================================================================


def test_the_next_action_row_says_what_its_date_means(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_matter(page, base_url, OPEN_TITLE)

    row = page.locator(".uxnext").first
    expect(row).to_be_visible()
    expect(row).to_contain_text("Järgmiseks")


def test_deferring_moves_the_date_and_says_which_day_it_lands_on(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_matter(page, base_url, OPEN_TITLE)

    defer = page.locator("details.uxnext__defer")
    if not defer.count():
        pytest.skip("this Matter's step carries no exact date to defer")

    defer.locator("summary").click()
    option = defer.locator("button[name=paevad][value='7']")
    expect(option).to_contain_text("·")  # the resolved weekday and date
    option.click()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#teema-vaade")).to_be_visible()


def test_the_defer_popover_closes_on_escape_and_returns_focus(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_matter(page, base_url, OPEN_TITLE)

    defer = page.locator("details.uxnext__defer")
    if not defer.count():
        pytest.skip("this Matter's step carries no exact date to defer")

    trigger = defer.locator("summary")
    trigger.click()
    assert defer.evaluate("node => node.open") is True
    page.keyboard.press("Escape")
    assert defer.evaluate("node => node.open") is False
    expect(trigger).to_be_focused()


# =========================================================================
# 1b — the timeline
# =========================================================================


def test_the_closed_timeline_carries_more_than_a_counter(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_matter(page, base_url, OPEN_TITLE)

    summary = page.locator(".accordion--timeline > summary")
    expect(summary).to_contain_text("kirjet")
    expect(summary.locator(".uxtl__preview")).to_be_visible()


def test_the_timeline_draws_one_spine(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_matter(page, base_url, OPEN_TITLE)

    # Open on arrival since the v2 rebuild (02-EKRAANID §C), so there is
    # nothing to click before the spine is on screen.
    expect(page.locator("#ajalugu-loend.uxtl")).to_be_visible()
    expect(page.locator(".uxtl__dot").first).to_be_visible()


# =========================================================================
# 1e — Minu töö
# =========================================================================


def test_j_and_k_move_the_selection_and_enter_opens_the_matter(page, base_url):
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/minu-asjad/")
    page.wait_for_load_state("networkidle")

    rows = page.locator("[data-workrow]")
    if rows.count() < 2:
        pytest.skip("this persona has fewer than two dated rows today")

    page.keyboard.press("j")
    assert "is-selected" in (rows.nth(0).get_attribute("class") or "")

    page.keyboard.press("j")
    assert "is-selected" in (rows.nth(1).get_attribute("class") or "")
    page.keyboard.press("k")
    assert "is-selected" in (rows.nth(0).get_attribute("class") or "")

    # `wait_for_url` rather than `networkidle`: the latter can return before the
    # navigation has started at all, which is a flake rather than a defect.
    page.keyboard.press("Enter")
    page.wait_for_url("**/teemad/**")


# The two browser tests that stood here are gone with the controls they pressed.
# The row's green ✓ and the `X` that pressed it left with the v2 design, and the
# `.uxkeys` hint strip with them (01-EHITUSJUHIS §3.6). Both tests had begun
# skipping themselves for a reason that read like an empty world, which is the
# one failure mode this suite must not have. The removal itself is asserted
# where it can be read rather than looked at: `tests/test_ux_pass.py::
# test_the_keyboard_hint_line_went_with_the_button_it_described` proves the
# strip, the row control and the `x` branch of `ux.js` went together, and
# `tests/test_person_workspace.py::test_no_complete_button` proves it again on
# the page. The route left with no caller is recorded as DS-02.


# =========================================================================
# 2d — saved views and assigning from the row
# =========================================================================


def test_a_saved_view_chip_is_a_shareable_link_that_narrows_the_register(page, base_url):
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/")
    page.wait_for_load_state("networkidle")

    chip = page.locator(".uxviews a.uxchip").filter(has_text="Vastutajata").first
    counted = int(chip.inner_text().rsplit("·", 1)[1].strip())
    chip.click()
    page.wait_for_load_state("networkidle")

    assert "vastutaja=puudub" in page.url
    expect(page.locator(".pagehead__context")).to_have_text(f"{counted} teemat")


def test_saving_the_current_view_hands_over_its_address(page, base_url):
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/?olek=avatud&ulatus=minu")
    page.wait_for_load_state("networkidle")

    page.get_by_text("+ Salvesta praegune filter vaatena").click()
    field = page.locator("#teemad-vaate-link")
    expect(field).to_be_visible()
    assert "ulatus=minu" in (field.input_value())


def test_an_owner_can_be_set_from_the_register_row(page, base_url):
    """Assigning from the row, which is the only place this gesture exists.

    `?kaupa=koik`: the v2 design set the register's default page size to twelve,
    and the seeded unassigned Matter carries the oldest reference — so on a
    world the functional suite has been filing into, the default ordering puts
    it on page two and it is not in the DOM at all. That is what made this test
    skip itself, with a message about an empty world that was not true
    (02-EKRAANID §C).

    Asserted rather than skipped for the same reason. The fixture is seeded
    unconditionally and nothing else in this suite assigns it, so its absence is
    a defect in the register or in the seed, and either is worth a red build.
    """
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/?olek=avatud&vastutaja=puudub&kaupa=koik")
    page.wait_for_load_state("networkidle")

    row = page.locator("tr").filter(has_text=UNASSIGNED_TITLE).first
    assert row.count(), (
        f"{UNASSIGNED_TITLE!r} is not in the unassigned register list, so the "
        f"gesture this test exists for cannot be exercised. The seeded world "
        f"files it with no owner and nothing else here assigns it."
    )

    row.locator("summary.uxassign__trigger").click()
    menu = row.locator(".uxassign__menu")
    expect(menu).to_be_visible()
    # The reader is offered first, and marked.
    expect(menu.locator("button").first).to_contain_text("(mina)")

    menu.locator("button").first.click()
    page.wait_for_load_state("networkidle")

    expect(page.locator(".message--success")).to_be_visible()
    # `kaupa=koik` here too: the search narrows by a word several Matters in a
    # busy world share, so twelve rows is not necessarily the twelve holding
    # this one.
    page.goto(f"{base_url}/teemad/?olek=avatud&kaupa=koik&q={UNASSIGNED_TITLE.split()[0]}")
    page.wait_for_load_state("networkidle")
    expect(page.locator("tr").filter(has_text=UNASSIGNED_TITLE).first).to_contain_text(
        SANDRA.short_name
    )


# =========================================================================
# Responsive
# =========================================================================


@pytest.mark.parametrize("width", WIDTHS)
def test_no_page_scrolls_sideways_at_any_supported_width(page, base_url, width):
    """The one exception is Osakond's team table, and it scrolls inside itself.

    Asserted for the head, because the head sees every page including the one
    with nine columns of numbers on it.
    """
    sign_in(page, base_url, HEAD)
    page.set_viewport_size({"width": width, "height": 900})

    for path in PAGES:
        page.goto(f"{base_url}{path}")
        page.wait_for_load_state("networkidle")
        assert not overflows(page), f"{path} overflows at {width}px"


@pytest.mark.parametrize("width", (1440, 1024, 375))
def test_the_matter_workspace_holds_its_width(page, base_url, width):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": 900})
    open_matter(page, base_url, OPEN_TITLE)

    assert not overflows(page), f"the Matter page overflows at {width}px"
    # The composer and the Järgmiseks row are both reachable and readable.
    expect(page.locator("details.uxcomp")).to_be_visible()
    expect(page.locator(".uxnext").first).to_be_visible()


@pytest.mark.parametrize("width", (480, 375))
def test_a_popover_stays_inside_a_narrow_viewport(page, base_url, width):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{base_url}/teemad/?olek=avatud&vastutaja=puudub")
    page.wait_for_load_state("networkidle")

    trigger = page.locator("summary.uxassign__trigger").first
    if not trigger.count():
        pytest.skip("nothing is unassigned in this world any more")
    trigger.click()

    box = page.locator(".uxassign__menu").first.bounding_box()
    assert box is not None
    assert box["x"] >= 0, "the menu starts off the left edge"
    assert box["x"] + box["width"] <= width + 1, "the menu runs off the right edge"
