"""Uus teema, read the way a lawyer reads it.

The database-level guarantees — cardinality, derived date meanings, the
canonical record a POST produces — are asserted in
`tests/test_matter_form_controls.py` and `tests/test_uus_teema_redesign.py`.
What only a browser shows is whether the page *reads* as those guarantees:
whether ticking a second Hetkeseis clears the first, whether a stage explains
itself on hover and on focus, and whether three rows of chips wrap instead of
taking the page sideways.

Both disclosures are gone with the redesign, so most of what used to be
"open the panel, then assert" is now "assert". What replaced them is a page
that shows everything at once, which is a stronger claim and a narrower one:
nothing here may need a click to become true.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, go_to, sign_in

pytestmark = pytest.mark.e2e

CREATE_PATH = "/teemad/uus/"


def create_form(page, base_url) -> None:
    page.goto(f"{base_url}{CREATE_PATH}")
    page.wait_for_load_state("networkidle")


def open_details(page, summary: str) -> None:
    page.locator("summary", has_text=summary).first.click()


# ---------------------------------------------------------------------------
# Hetkeseis and Menetlusliik
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["stage", "track", "addressee_organisation"])
def test_the_procedural_fields_are_visible_choices_not_dropdowns(page, base_url, field):
    """For a department of four, a select is a click spent finding out what the
    options even are.

    And no longer behind "+ Täpsusta teema andmeid": the procedural half of the
    page is on the page. `attached` rather than `visible` for the radio, because
    the chip control hides the input and shows the label — which is the next
    test's subject.
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    expect(page.locator(f'select[name="{field}"]')).to_have_count(0)
    expect(page.locator(f'input[type="radio"][name="{field}"]').first).to_be_attached()
    expect(page.locator("summary", has_text="Täpsusta teema andmeid")).to_have_count(0)


def test_the_chip_hides_the_box_and_keeps_the_control(page, base_url):
    """The chip is a skin, not a replacement.

    The native input is still there, still focusable and still the thing a
    click lands on — it covers its chip at zero opacity rather than being
    shrunk to nothing, because a control with no box is a control a browser
    reports as invisible and a driver refuses to touch. What moved is that the
    label carries the state instead of a 13px box beside it
    (Uus teema redesign §4, static/css/app.css `.chip__input`).
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    chip = page.locator(".chip").first
    box = chip.locator("input")
    expect(box).to_be_attached()

    painted = box.evaluate(
        """node => {
             const style = getComputedStyle(node);
             const own = node.getBoundingClientRect();
             const chip = node.closest('.chip').getBoundingClientRect();
             return {
               opacity: style.opacity,
               covers: Math.abs(own.width - chip.width) < 2
                       && Math.abs(own.height - chip.height) < 2,
             };
           }"""
    )
    assert painted["opacity"] == "0", painted
    assert painted["covers"], painted

    expect(chip.locator(".chip__name")).to_be_visible()

    # And clicking the chip is clicking the control. The click lands on the
    # input, which is what covering it is for — a driver asked to click the
    # *name* is correctly told that something else is in front of it.
    chip.click()
    expect(box).to_be_checked()


@pytest.mark.parametrize("field", ["stage", "track"])
def test_choosing_a_second_value_replaces_the_first(page, base_url, screenshots, field):
    """The cardinality promise, seen rather than inferred.

    `Matter.stage` and `Matter.track` hold one value each. A control that let
    two stay ticked would be promising something the model cannot keep — and a
    checkmark on a card is decoration on a radio, not a second checkbox.
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    options = page.locator(f'input[type="radio"][name="{field}"]')
    if options.count() < 3:
        pytest.skip(f"this world offers fewer than two real {field} values")

    # Index 0 is the named blank option; the two after it are real values.
    options.nth(1).check()
    expect(options.nth(1)).to_be_checked()
    options.nth(2).check()

    expect(options.nth(2)).to_be_checked()
    expect(options.nth(1)).not_to_be_checked()
    if field == "stage":
        screenshots(page, "teema-hetkeseisu-valik")


def test_several_policy_areas_can_be_ticked_at_once(page, base_url):
    """The inverse promise: a Matter really can belong to several areas."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    boxes = page.locator('input[type="checkbox"][name="policy_areas"]')
    if boxes.count() < 2:
        pytest.skip("this world has fewer than two policy areas")

    boxes.nth(0).check()
    boxes.nth(1).check()
    expect(boxes.nth(0)).to_be_checked()
    expect(boxes.nth(1)).to_be_checked()


# ---------------------------------------------------------------------------
# The sender disclosure
# ---------------------------------------------------------------------------


def test_the_other_sender_panel_does_not_repeat_the_chips_above_it(page, base_url):
    """ "Muu / lisa saatja" used to reopen the same list, which read as a second
    sender control disagreeing with the first."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    frequent = page.locator('input[type="checkbox"][name="source_organisations"]')
    expect(frequent.first).to_be_visible()
    chips = set(frequent.evaluate_all("nodes => nodes.map(node => node.value)"))

    open_details(page, "Vali nimekirjast")
    rest = page.locator('input[type="checkbox"][name="source_organisations_other"]')
    listed = set(rest.evaluate_all("nodes => nodes.map(node => node.value)"))

    assert chips.isdisjoint(listed), "the disclosure repeats the chips above it"


def test_the_form_says_where_a_missing_institution_comes_from(page, base_url):
    """Rather than inviting somebody to type a second spelling of a ministry
    into a matter form and mint a reference record with it."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)
    open_details(page, "Vali nimekirjast")

    # `.first`, because the page holds more than one disclosure body and
    # Playwright refuses an ambiguous locator rather than picking one.
    expect(page.get_by_text("asutuste alla", exact=False).first).to_be_visible()


# ---------------------------------------------------------------------------
# Järgmine tegevus
# ---------------------------------------------------------------------------


def test_the_three_kinds_are_the_shapes_they_are_everywhere_else(page, base_url, screenshots):
    """TEEN filled, OOTAN solid-outlined, JÄLGIN dashed.

    The same three chips as Minu töö, the register and the Teema composer, and
    the same rule: shape carries the distinction, colour never alone. It
    replaces three described cards whose glosses explained a vocabulary the
    reader has now met on four other surfaces (Uus teema redesign §6).
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    block = page.locator("#jargmine-liik")
    expect(block).to_be_visible()
    for value in ("do", "wait", "monitor"):
        expect(block.locator(f".modechip--{value}")).to_have_count(1)

    borders = block.evaluate(
        """node => ['do', 'wait', 'monitor'].map(kind =>
              getComputedStyle(node.querySelector('.modechip--' + kind)).borderStyle)"""
    )
    assert borders[2] == "dashed", borders
    assert borders[0] == "solid" and borders[1] == "solid", borders
    screenshots(page, "jargmine-tegevus")


def test_only_one_kind_can_be_active(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    wait = page.locator('input[name="next-kind"][value="WAIT"]')
    monitor = page.locator('input[name="next-kind"][value="MONITOR"]')
    wait.check()
    expect(wait).to_be_checked()
    monitor.check()
    expect(monitor).to_be_checked()
    expect(wait).not_to_be_checked()


@pytest.mark.parametrize(
    ("value", "meaning"),
    [
        ("DO", "DEADLINE"),
        ("WAIT", "EXPECTED_AROUND"),
        ("MONITOR", "REVIEW_ON"),
    ],
)
def test_the_date_meaning_follows_the_chosen_kind(page, base_url, value, meaning):
    """The stored meaning, stated on the row rather than asked about.

    It was a nested disclosure headed "Täpsusta, mida kuupäev tähendab" over a
    select called "Kuupäeva tähendus" — a question in the vocabulary of the
    database. Three chips beside the date say the same thing in the vocabulary
    of the work, and the chosen one still derives from the kind.
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    page.locator(f'input[name="next-kind"][value="{value}"]').check()
    expect(page.locator(f'input[name="next-date_semantics"][value="{meaning}"]')).to_be_checked()


def test_a_chosen_meaning_survives_a_change_of_kind(page, base_url):
    """Once somebody has said what the date means, it is theirs.

    The model permits pairs the derivation does not produce — a DO whose source
    names a vague month is an expectation, not a deadline — and the register's
    own parser records them. A derivation that overwrote an explicit choice
    would make those unreachable from the page.
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    around = page.locator('input[name="next-date_semantics"][value="EXPECTED_AROUND"]')
    around.check()
    page.locator('input[name="next-kind"][value="MONITOR"]').check()

    expect(around).to_be_checked()


def test_the_two_deadlines_are_two_places_on_the_page(page, base_url):
    """Arvamuse tähtaeg is when this opinion must go out; Järgmine tegevus is
    what happens next with the file.

    A paragraph used to say so, because both were behind disclosures and a
    reader could have only one of them on screen. Both are visible now — one a
    labelled date beside Saabus, the other a panel of its own — so the layout
    says it (Uus teema redesign §7).
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    expect(page.locator('input[name="response_deadline"]')).to_be_visible()
    expect(page.locator("#jargmine-tegevus")).to_be_visible()
    expect(page.get_by_text("Arvamuse tähtaeg on eraldi")).to_have_count(0)


# ---------------------------------------------------------------------------
# Hetkeseis explains itself
# ---------------------------------------------------------------------------


def _open_bubbles(page):
    return page.evaluate(
        """() => [...document.querySelectorAll('.stagehelp')]
              .filter(node => getComputedStyle(node).display !== 'none')
              .map(node => ({
                id: node.id,
                text: node.textContent.trim(),
                clipped: node.getBoundingClientRect().right
                           > document.documentElement.clientWidth + 1
                         || node.getBoundingClientRect().left < -1,
              }))"""
    )


def test_a_stage_explains_itself_on_hover(page, base_url, screenshots):
    """Which of `Kooskõlastusringil` and `Valitsuses` a file is in depends on an
    event that has or has not happened, and the department wrote a sentence per
    stage saying which (Uus teema redesign §8)."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    assert _open_bubbles(page) == []

    # By the radio's accessible name, not by a substring of the chip's text.
    # `has_text` is a case-insensitive substring, and the Idee explanation
    # contains "kooskõlastusringile" — so the first chip *containing* the word
    # is the wrong one. The name is the three words on the chip, which is what
    # moving the bubble out of the label restored.
    radio = page.get_by_role("radio", name="Kooskõlastusringil", exact=True)
    radio.hover()
    page.wait_for_timeout(120)

    shown = _open_bubbles(page)
    assert len(shown) == 1, shown
    assert shown[0]["text"].startswith("Seaduse või määruse eelnõu kooskõlastusringile")
    assert not shown[0]["clipped"]
    screenshots(page, "hetkeseisu-selgitus")

    page.mouse.move(0, 0)
    page.wait_for_timeout(150)
    assert _open_bubbles(page) == []


def test_each_stage_shows_its_own_text_and_only_its_own(page, base_url):
    """One chip, one bubble. A row that opened two would be a row explaining
    something other than what the pointer is on."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    chips = page.locator(".chip--explained")
    assert chips.count() >= 5

    for index in range(chips.count()):
        # Hovering the input rather than the wrapper: the input covers its chip
        # and is what the pointer meets, and the wrapper also contains the
        # bubble, whose own box a hover would otherwise land in.
        chips.nth(index).locator("input").hover()
        page.wait_for_timeout(60)
        shown = _open_bubbles(page)
        assert len(shown) == 1, (index, shown)
        assert not shown[0]["clipped"], shown[0]["id"]


def test_the_explanation_reaches_a_keyboard_and_a_screen_reader(page, base_url):
    """Hover is not the only way in.

    Focus opens the same bubble, Escape closes it, and the radio points at it
    with `aria-describedby` — which is what makes the text part of the option's
    accessible description rather than a hint only a mouse can find.
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    radio = page.locator('input[name="stage"][aria-describedby]').first
    described = radio.get_attribute("aria-describedby")
    assert described

    radio.focus()
    page.wait_for_timeout(120)
    shown = _open_bubbles(page)
    assert [node["id"] for node in shown] == [described], shown

    page.keyboard.press("Escape")
    page.wait_for_timeout(120)
    assert _open_bubbles(page) == []


@pytest.mark.parametrize("width", [1440, 1280, 1024])
def test_a_stage_tooltip_never_opens_off_the_screen(page, base_url, width):
    """The chips wrap, so the last one on a row sits against the right edge.

    Its bubble flips to open leftwards rather than widening the document, which
    is the difference between a tooltip and a horizontal scrollbar.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    create_form(page, base_url)

    chips = page.locator(".chip--explained")
    for index in range(chips.count()):
        chips.nth(index).locator("input").hover()
        page.wait_for_timeout(60)
        shown = _open_bubbles(page)
        assert shown and not shown[0]["clipped"], (width, index, shown)
        assert not _document_overflows(page), f"a tooltip widened the page at {width}px"


def test_the_next_action_block_is_still_optional(page, base_url):
    """A form that shows a field is not thereby a form that requires it.

    An opinion being drafted already has its Arvamuse tähtaeg, and a synthetic
    next action invented to satisfy the layout would be a record of an
    intention nobody had.
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    page.fill("#id_title", "Ilma järgmise sammuta brauserist")
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    expect(page.locator(".uxnext")).to_contain_text("Järgmine samm on määramata")


def test_a_next_action_created_here_takes_the_chosen_owner(page, base_url):
    """Nobody should have to name the same colleague twice on one form."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    page.fill("#id_title", "Vastutaja pärandub järgmisele sammule")
    page.locator('input[name="owner"]').first.check()
    page.fill("#id_next-text", "Jälgi menetluse käiku")
    page.locator('input[name="next-kind"][value="MONITOR"]').check()
    page.fill("#id_next-target_date", "1.9.2026")
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    # The row itself no longer prints the responsible person: on a Matter page
    # the owner is already in the header meta line, and repeating it beside the
    # step was one of the six values the row was carrying instead of the
    # sentence (Teema redesign §8). The service still assigns one, which is what
    # this test is actually about, so it is checked where it is visible: the
    # step appears in that person's own Minu töö queue.
    expect(page.locator(".uxnext__text")).to_have_text("Jälgi menetluse käiku")
    go_to(page, "Minu töö")
    expect(page.get_by_text("Jälgi menetluse käiku").first).to_be_visible()


# ---------------------------------------------------------------------------
# Narrow windows
# ---------------------------------------------------------------------------


def _document_overflows(page) -> bool:
    """Mirrors `e2e/test_ui_shell.py`, because the rule is the same one.

    Wide content scrolls inside its own container; the document itself never
    scrolls sideways.
    """
    return page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )


@pytest.mark.parametrize("width", [1440, 1280, 1024, 420])
def test_the_form_survives_a_narrow_window(page, base_url, width):
    """Six rows of chips now, and nothing hidden behind a disclosure.

    Vastutaja, Saatja, twenty-two Valdkonnad, eleven Hetkeseis, eight
    Menetlusliik and Adressaat — plus the three mode chips and the three date
    meanings. A chip that refused to wrap would take the whole page sideways
    with it, and at 1024 the paired rows have to stop being pairs.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    create_form(page, base_url)
    page.wait_for_load_state("networkidle")

    assert not _document_overflows(page), f"the create form scrolls sideways at {width}px"


@pytest.mark.parametrize("width", [1440, 1280, 1024])
def test_the_whole_form_is_reachable_without_opening_anything(page, base_url, width):
    """The claim the redesign is built on: everything is on screen at load.

    Not "everything fits on one screen" — it does not, and the design says so.
    What must hold is that no field needs a click to *exist*, so a reader who
    scrolls has seen the whole form.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    create_form(page, base_url)

    for name in (
        "title",
        "brief_summary",
        "notes",
        "files",
        "received_date",
        "response_deadline",
        "policy_area_other_selected",
        "is_test_data",
        "next-text",
        "next-target_date",
    ):
        expect(page.locator(f'[name="{name}"]')).to_have_count(1)
    for group in ("owner", "source_organisations", "policy_areas", "stage", "track"):
        expect(page.locator(f'[name="{group}"]').first).to_be_attached()

    expect(page.get_by_role("button", name="Loo teema")).to_be_visible()


def test_the_submit_reads_inactive_without_becoming_unusable(page, base_url):
    """The flat fill is a hint, not a claim.

    `disabled` would strand a browser with scripting off, and `aria-disabled`
    would tell a screen reader the button is unavailable while the server is
    perfectly willing to answer it. Pressing it with no title has to produce the
    refusal beside the field (Uus teema redesign §7, §15).
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    button = page.get_by_role("button", name="Loo teema")
    expect(button).to_have_attribute("data-inactive", "true")
    expect(button).to_be_enabled()
    assert button.get_attribute("aria-disabled") is None

    page.fill("#id_title", "Nüüd on pealkiri")
    expect(button).to_have_attribute("data-inactive", "false")


def test_a_refused_save_hides_nothing_it_was_given(page, base_url):
    """A refusal must not cost somebody the fields they filled in — and must
    not need a click to show them what went wrong."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    page.fill("#id_brief_summary", "Mida see teema ettevõtjatele tähendab.")
    page.locator('input[name="policy_areas"]').first.check()
    page.locator("form.createform").evaluate("form => form.noValidate = true")
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    expect(page.locator("#id_brief_summary")).to_have_value(
        "Mida see teema ettevõtjatele tähendab."
    )
    expect(page.locator('input[name="policy_areas"]').first).to_be_checked()
    expect(page.locator(".field__error").first).to_be_visible()


@pytest.mark.parametrize("width", [1024, 420])
def test_the_seis_strip_survives_a_narrow_window(page, base_url, width):
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{base_url}/ulevaade/")
    page.wait_for_load_state("networkidle")

    # The KPI cards became a one-line strip with the work-surface rebuild.
    # What is asserted is unchanged: the figures are still there and the page
    # still does not scroll sideways.
    expect(page.locator(".seis__figure").first).to_be_visible()
    assert not _document_overflows(page), f"Ülevaade scrolls sideways at {width}px"


def test_the_choice_cards_are_real_controls_with_real_labels(page, base_url):
    """No click-only divs. Every chip is a label bound to an input, which is
    what makes the row keyboard-reachable and readable to a screen reader."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    for name in ("stage", "track", "owner", "policy_areas", "addressee_organisation"):
        inputs = page.locator(f'input[name="{name}"]')
        expect(inputs.first).to_be_attached()
        # Wrapped in their own <label>, so the whole chip is the hit area and
        # the accessible name is the chip's text.
        wrapped = inputs.first.evaluate("node => node.closest('label') !== null")
        assert wrapped, f"{name} chips are not inside a label"
