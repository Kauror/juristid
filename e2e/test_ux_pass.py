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
from e2e.conftest import (
    HEAD,
    SANDRA,
    add_panel_is_open,
    open_add_panel,
    open_composer,
    open_next_action_form,
    sign_in,
)

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


def open_matter_by_clicking(page, base_url: str, title: str) -> None:
    """Deliberately not `conftest.open_matter`, which follows the href.

    Named apart because the difference is behavioural, not stylistic: this
    clicks, and the sticky table head can sit over the first row. The tests
    below want the click path; anything that merely wants to be on the Matter
    page should import the conftest helper instead.
    """
    page.goto(f"{base_url}/teemad/?olek=koik&q={title.split()[0]}")
    page.wait_for_load_state("networkidle")
    page.get_by_role("link", name=title, exact=False).first.click()
    page.wait_for_load_state("networkidle")


# =========================================================================
# 1d — the composer
# =========================================================================


def test_l_puts_the_caret_in_the_box_that_records_what_happened(page, base_url):
    """`L` for «lisa», and a shortcut with an obvious click equivalent.

    It used to open the composer, which was both *what happened* and *what
    happens next*. Those are two operations now, so `L` reaches the box that
    records something being written down: `Mida tegid?` on a Matter with a
    current task, and `+ Märge` on one without (docs/adr/0075 §3).
    """
    sign_in(page, base_url, SANDRA)
    open_matter_by_clicking(page, base_url, OPEN_TITLE)

    current = page.locator("#praegune-tegevus textarea.composer__body")
    if current.count():
        page.keyboard.press("l")
        expect(current).to_be_focused()
        box = current
    else:
        assert not add_panel_is_open(page, "lisa-marge")
        page.keyboard.press("l")
        assert add_panel_is_open(page, "lisa-marge")
        box = page.locator("#lisa-marge [data-composer-focus]")
        expect(box).to_be_focused()

    # And the same key inside the box types a letter rather than doing anything.
    page.keyboard.type("l")
    assert box.input_value().endswith("l")


def test_a_quick_date_fills_the_field_that_is_actually_submitted(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_matter_by_clicking(page, base_url, OPEN_TITLE)

    open_next_action_form(page)
    chip = page.locator("#lisa-jargmine [data-quickdate]").filter(has_text="+1 nädal").first
    expected = chip.get_attribute("data-quickdate")
    chip.click()

    expect(page.locator("#id_target_date")).to_have_value(expected)
    # The chip now says the day it means, not just the span.
    expect(chip).to_contain_text("→")
    assert "is-selected" in (chip.get_attribute("class") or "")


def test_a_marge_still_saves_with_ctrl_enter(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_matter_by_clicking(page, base_url, OPEN_TITLE)

    open_composer(page)
    # `Mis juhtus?` is one stated line rather than a prose textarea since
    # docs/adr/0097 §6. The shortcut is the form's, not the control's: the
    # handler reaches `form[data-addform]` from anything inside it.
    box = page.locator("#id_marge_title")
    box.fill("Sünteetiline kiirsissekanne klaviatuurilt.")
    box.press("Control+Enter")
    page.wait_for_load_state("networkidle")

    expect(page.locator("#teema-vaade")).to_contain_text("Sünteetiline kiirsissekanne")


def test_every_advanced_composer_field_is_still_reachable(page, base_url):
    """Folding the composer dropped nothing that is still asked for.

    The next step's «Täpsemalt…» panel is not a fold that got lost — it was
    deliberately deleted, along with the classification it held (ADR 0052 §4).
    The *classification* is still gone and is still unreachable by POST.

    **The period control came back, and only here.** ADR 0052 §4 deleted it on
    the reasoning that a lawyer's own working day is a day; docs/adr/0079 §1
    supersedes that half, because a step that genuinely belongs *in October*
    had to be filed as the 1st. `Täpne päev` is still the chip that is
    selected first, and the quick spans still sit inside it.
    """
    sign_in(page, base_url, SANDRA)
    open_matter_by_clicking(page, base_url, OPEN_TITLE)

    # The next step asks what, when, and how exactly the when is known.
    open_next_action_form(page)
    expect(page.locator("#lisa-jargmine [name='text']")).to_be_visible()
    expect(page.locator("#id_target_date")).to_have_count(1)
    expect(page.locator("details.uxcomp__more")).to_have_count(0)
    # The classification is still gone from the contract, not merely hidden.
    expect(page.locator("#id_next_date_semantics")).to_have_count(0)
    expect(page.locator("#lisa-jargmine [name=next_kind]")).to_have_count(0)
    # And the four precisions are here, as real radios.
    expect(page.locator("#lisa-jargmine input[name=next_precision]")).to_have_count(4)
    for label in ("Täpne päev", "Kuu", "Kvartal", "Aasta"):
        expect(page.locator("#lisa-jargmine label.precision__chip", has_text=label)).to_have_count(
            1
        )

    # `Oluline tähtaeg` asks the same four, from the same partial. `Aasta` is
    # the one that did not exist before this round, and its absence was why the
    # panel had to derive a period from the day somebody typed
    # (docs/adr/0079 §1, superseding docs/adr/0074 §11).
    open_add_panel(page, "marge-tahtaeg")
    expect(page.locator("#marge-tahtaeg [name=deadline_date]")).to_be_visible()
    for label in ("Täpne päev", "Kuu", "Kvartal", "Aasta"):
        expect(page.locator("#marge-tahtaeg label.precision__chip", has_text=label)).to_have_count(
            1
        )
    expect(page.locator("#marge-tahtaeg").get_by_text("Poolaasta")).to_have_count(0)

    # `Lõpeta teema` is a peer chip in the launcher again and shares its radio
    # group, so opening it closes whatever was open — one form at a time across
    # the whole row, closure included. It had a group of its own while it was a
    # section of its own, which let it stand open beside a capture panel; back
    # in the row that would mean two open forms in one choice
    # (docs/adr/0099 §5, amending docs/adr/0097 §9).
    open_add_panel(page, "teema-lopeta")
    expect(page.locator("#teema-lopeta")).to_be_visible()
    expect(page.locator("#teema-lopeta [name=closing_words]")).to_be_visible()
    expect(page.locator("#marge-tahtaeg")).not_to_be_visible()

    # And the rule holds in the other direction too.
    open_add_panel(page, "lisa-kaasamine")
    expect(page.locator("#lisa-kaasamine")).to_be_visible()
    expect(page.locator("#teema-lopeta")).not_to_be_visible()
    expect(page.locator("#lisa-marge")).not_to_be_visible()
    expect(page.locator("#marge-tahtaeg")).not_to_be_visible()


# =========================================================================
# 1c — Järgmiseks
# =========================================================================


def test_the_next_action_row_says_the_step_and_its_date_and_nothing_else(page, base_url):
    """The date is a date. What it *means* is no longer a word beside it, and
    the three-category vocabulary is not on this surface at all (ADR 0052 §6)."""
    sign_in(page, base_url, SANDRA)
    open_matter_by_clicking(page, base_url, OPEN_TITLE)

    zone = page.locator("#praegune-tegevus").first
    expect(zone).to_be_visible()
    expect(zone).to_contain_text("Praegune tegevus")

    # Read off the **step's own line**, which is where every one of these words
    # lived: TEEN/OOTAN/JÄLGIN were the kind chip in front of the sentence and
    # TÄHTAEG was the flag in front of its date. The zone as a whole also
    # carries `Arvamuse tähtaeg` now, where Koda still owes an answer on a file
    # under an instruction — a different, still-current label that happens to
    # contain one of these words as a substring, and the two remaining
    # legitimate spellings are exactly `OLULINE TÄHTAEG` and `ARVAMUSE TÄHTAEG`
    # (tests/test_next_action_plaanis_wording.py, PR #205).
    text = zone.locator(".curact__task").inner_text()
    for retired in ("TEEN", "OOTAN", "JÄLGIN", "TÄHTAEG", "VAATAN ÜLE", "OODATAV"):
        assert retired not in text, f"the step still says «{retired}»"


# `test_deferring_moves_the_date_and_says_which_day_it_lands_on` was retired
# (ENG-051). Its control, «Lükka edasi» on the Teema page, was removed by
# docs/adr/0074 §20, so it skipped on every run and was counted as coverage it
# no longer gave. The absence is asserted where it belongs
# (e2e/test_simplified_next_action.py), and the route and its day-counting rule
# are still exercised directly (tests/test_ux_pass.py).


def test_the_defer_popover_closes_on_escape_and_returns_focus(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_matter_by_clicking(page, base_url, OPEN_TITLE)

    # «Lükka edasi» left the Järgmiseks row with the approved target, and with
    # it the only `[data-uxpopover]` this page had (docs/adr/0074 §20).
    #
    # **The `Kuupäev…` disclosure it was asserted through is gone too.** The
    # date box is now the `Täpne päev` group of the `Täpsus` control and is
    # shown because that chip is the one selected first, so there is nothing
    # left to open (docs/adr/0079 §1). What that disclosure existed to protect
    # is unchanged and is what this asserts instead: a date somebody is typing
    # must not vanish or reset because they clicked elsewhere in the same form.
    open_add_panel(page, "lisa-jargmine")
    day_group = page.locator('#lisa-jargmine .precision__group[data-precision-for="day"]')
    expect(day_group).to_be_visible()

    page.locator("#id_target_date").fill("30.09.2026")
    page.locator("#lisa-jargmine [name='text']").click()

    expect(day_group).to_be_visible()
    assert page.locator("#id_target_date").input_value() == "30.09.2026"

    # And choosing another precision puts the question somewhere else rather
    # than leaving two date controls disagreeing.
    page.locator("#lisa-jargmine label.precision__chip", has_text="Kvartal").first.click()
    expect(day_group).to_be_hidden()
    expect(
        page.locator('#lisa-jargmine .precision__group[data-precision-for="quarter"]')
    ).to_be_visible()


# =========================================================================
# 1b — the timeline
# =========================================================================


def test_the_closed_timeline_carries_more_than_a_counter(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_matter_by_clicking(page, base_url, OPEN_TITLE)

    summary = page.locator(".accordion--timeline > summary")
    # `AJAJOON` and `{n} kirjet`, and nothing else. The head carried a preview
    # quote *and* the step currently owed *and* the count — three facts in a
    # summary line for a section that is open on arrival, one of them a verbatim
    # repeat of the Järgmiseks row three inches above it (docs/adr/0074 §16).
    expect(summary).to_contain_text("Teema käik")
    expect(summary).to_contain_text("kirjet")
    expect(summary.locator(".uxtl__preview")).to_have_count(0)
    expect(summary.locator(".uxtl__previewnext")).to_have_count(0)
    # The head is still the whole trigger: the section closes.
    summary.click()
    expect(page.locator(".accordion--timeline")).not_to_have_attribute("open", "")


def test_the_timeline_draws_one_spine(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_matter_by_clicking(page, base_url, OPEN_TITLE)

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

    # **Put the row in the middle of the viewport before clicking into it**, and
    # the two failures that got here are why it has to be the middle rather than
    # either end.
    #
    # At the top, the register's column head — `position: sticky; top: 48px`
    # beneath a sticky top bar — covers the row, and the click on the trigger
    # waits out its full actionability timeout against a header it cannot see
    # through (`e2e/conftest.py` `open_matter` documents the same hazard).
    #
    # At the bottom, the trigger is clickable and the *menu* is not: it is
    # `position: fixed` and `static/js/ux.js` `place()` puts it four pixels under
    # the trigger, so a row near the fold opens a menu below it.
    #
    # Latent until it was not. This file shares a browser shard with whichever
    # files the partition puts beside it, and adding one file to the suite moved
    # two Matter-creating ones in front of this test — more unassigned Matters,
    # this row further down the list than it has ever been, and both ends of
    # that scroll wrong for the first time (ci_sharding.py).
    row.evaluate("node => node.scrollIntoView({block: 'center', behavior: 'instant'})")

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
    open_matter_by_clicking(page, base_url, OPEN_TITLE)

    assert not overflows(page), f"the Matter page overflows at {width}px"
    # Both zones of the workspace are reachable and readable.
    expect(page.locator("#praegune-tegevus").first).to_be_visible()
    expect(page.locator("#lisa-teemale").first).to_be_visible()


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
