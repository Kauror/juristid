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

from datetime import date, timedelta

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
# Järgmiseks
# ---------------------------------------------------------------------------


def _panel(page):
    return page.locator("#jargmine-tegevus")


def test_the_panel_asks_two_questions_and_names_neither_vocabulary(page, base_url, screenshots):
    """`Järgmiseks`, `Millal?`, and the four spans — and nothing to classify.

    This replaces two assertions: three mode chips in the shapes TEEN / OOTAN /
    JÄLGIN carry, and three meaning chips beside the date. Both were a
    vocabulary this application introduced rather than one the department used,
    and native creation no longer asks for either (ADR 0052).

    Scoped to the panel throughout. The page legitimately contains the word
    *tähtaeg* — `Arvamuse tähtaeg` is a different fact, further up the form —
    so a whole-page assertion that it is absent would fail on the wrong thing.
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    panel = _panel(page)
    expect(panel).to_be_visible()
    for asked in ("Järgmiseks", "Millal?", "Täna", "Homme", "+1 nädal", "+2 nädalat"):
        expect(panel).to_contain_text(asked)
    expect(panel.locator("summary", has_text="Kuupäev…")).to_have_count(1)

    for retired in ("TEEN", "OOTAN", "JÄLGIN", "Tähtaeg", "Oodatav aeg", "Vaatan üle"):
        expect(panel).not_to_contain_text(retired)
    expect(panel.locator('[name="next-kind"]')).to_have_count(0)
    expect(panel.locator('[name="next-date_semantics"]')).to_have_count(0)
    expect(panel.locator(".modechip")).to_have_count(0)

    screenshots(page, "jargmine-tegevus")


def test_the_date_box_starts_empty(page, base_url):
    """A blank form must not silently contain today as a next-action date."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    expect(page.locator("#id_next-target_date")).to_have_value("")


@pytest.mark.parametrize(
    ("label", "days"),
    [("Täna", 0), ("Homme", 1), ("+1 nädal", 7), ("+2 nädalat", 14)],
)
def test_a_quick_span_writes_its_day_into_the_one_date_field(page, base_url, label, days):
    """The chips store nothing of their own.

    Each writes the day the *server* resolved for it into the exact-date box,
    which is the field that is submitted and validated — so the form works with
    the chips ignored entirely, and the arithmetic never happens in the
    reader's timezone (app/matters/views.py `quick_date_choices`).
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    chip = _panel(page).get_by_role("button", name=label, exact=False).first
    expected = chip.get_attribute("data-quickdate")
    # The server's arithmetic, checked against the browser's own clock rather
    # than restated from it: the page is authoritative, and this is what says so.
    wanted = date.today() + timedelta(days=days)
    assert expected == f"{wanted.day}.{wanted.month}.{wanted.year}", expected
    chip.click()

    expect(page.locator("#id_next-target_date")).to_have_value(expected)
    expect(chip).to_have_attribute("aria-pressed", "true")

    # And it is the value that is actually stored, not merely the value that is
    # shown: the chip writes into the submitted field and nothing else does.
    page.fill("#id_title", f"Kiirvalik {label}")
    page.fill("#id_next-text", f"Vaadata eelnõu üle ({label})")
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    expect(page.locator(".uxnext__text")).to_have_text(f"Vaadata eelnõu üle ({label})")
    # `.uxnext__date` rather than the whole row: the row also carries the
    # «Lükka edasi» menu, whose options print dates of their own.
    expect(page.locator(".uxnext__date")).to_contain_text(expected)


def test_the_exact_box_behind_kuupaev_takes_a_typed_date(page, base_url):
    """A real `<details>`, so the exact date is reachable and submittable with
    scripting switched off entirely."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    page.fill("#id_title", "Käsitsi kuupäev")
    page.fill("#id_next-text", "Vaadata uus eelnõu versioon üle")
    _panel(page).locator("summary", has_text="Kuupäev…").click()
    page.fill("#id_next-target_date", "1.9.2026")
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    expect(page.locator(".uxnext__text")).to_have_text("Vaadata uus eelnõu versioon üle")
    # The date this application writes: `1.9.2026`, no leading zeros, and read
    # off the step's own element rather than the row — the «Lükka edasi» menu
    # beside it prints zero-padded days of its own (app/core/dates.py).
    expect(page.locator(".uxnext__date")).to_contain_text("1.9.2026")


def test_a_step_with_no_date_is_refused_and_the_text_survives(page, base_url):
    """The refusal a person actually meets, and what they get back."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    page.fill("#id_title", "Kuupäevata samm brauserist")
    page.fill("#id_next-text", "Vaadata uus eelnõu versioon üle")
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    expect(_panel(page)).to_contain_text("Vali järgmise tegevuse kuupäev.")
    expect(page.locator("#id_next-text")).to_have_value("Vaadata uus eelnõu versioon üle")
    expect(page.locator("#id_title")).to_have_value("Kuupäevata samm brauserist")


def test_a_date_with_no_step_is_refused_and_the_date_survives(page, base_url):
    """The other half, and the one the old page dropped in silence.

    Pressing `Homme` and then forgetting the sentence used to create the Teema
    without the step — the view read `next-text` alone to decide whether one had
    been asked for. A chosen date is a decision somebody made, so it is answered
    rather than discarded (ADR 0052 addendum).
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    page.fill("#id_title", "Sammuta kuupäev brauserist")
    chip = _panel(page).get_by_role("button", name="Homme", exact=False).first
    chosen = chip.get_attribute("data-quickdate")
    chip.click()
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    expect(_panel(page)).to_contain_text("Kirjuta järgmine tegevus.")
    expect(page.locator("#id_next-target_date")).to_have_value(chosen)
    # And the box holding it is open, because a value redisplayed inside a
    # closed «Kuupäev…» is a value nobody can see they still have.
    expect(page.locator("#id_next-target_date")).to_be_visible()


def test_the_two_deadlines_are_two_places_on_the_page(page, base_url):
    """Arvamuse tähtaeg is when this opinion must go out; Järgmiseks is what
    happens next with the file.

    A paragraph used to say so, because both were behind disclosures and a
    reader could have only one of them on screen. Both are visible now — one a
    labelled date beside Saabus, the other a panel of its own — so the layout
    says it (Uus teema redesign §7).
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    expect(page.locator('input[name="response_deadline"]')).to_be_visible()
    expect(_panel(page)).to_be_visible()
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
    page.fill("#id_next-text", "Jälgida menetluse käiku")
    _panel(page).locator("summary", has_text="Kuupäev…").click()
    page.fill("#id_next-target_date", "1.9.2026")
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    # The row itself no longer prints the responsible person: on a Matter page
    # the owner is already in the header meta line, and repeating it beside the
    # step was one of the six values the row was carrying instead of the
    # sentence (Teema redesign §8). The service still assigns one, which is what
    # this test is actually about, so it is checked where it is visible: the
    # step appears in that person's own Minu töö queue.
    expect(page.locator(".uxnext__text")).to_have_text("Jälgida menetluse käiku")
    go_to(page, "Minu asjad")
    expect(page.get_by_text("Jälgida menetluse käiku").first).to_be_visible()


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


@pytest.mark.parametrize("width", [1440, 1280, 1024, 768, 420])
def test_the_form_survives_a_narrow_window(page, base_url, width):
    """Five rows of chips now, and nothing hidden behind a disclosure.

    Vastutaja, Saatja, twenty-two Valdkonnad, eleven Hetkeseis, eight
    Menetlusliik and Adressaat — plus the four quick spans and the «Kuupäev…»
    disclosure on the `Millal?` row. A chip that refused to wrap would take the
    whole page sideways with it, and at 1024 the paired rows have to stop being
    pairs.

    The mode row and the meaning row are gone from this list rather than from
    the count of things that must wrap: the `Millal?` row is the one that has
    to fold now, and 768 was added because that is where it starts to.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    create_form(page, base_url)
    page.wait_for_load_state("networkidle")

    assert not _document_overflows(page), f"the create form scrolls sideways at {width}px"

    # The two controls a narrow window most easily strands: the disclosure that
    # holds the exact date, and the action that submits the form.
    expect(_panel(page).locator("summary", has_text="Kuupäev…")).to_be_visible()
    expect(page.get_by_role("button", name="Loo teema")).to_be_visible()


@pytest.mark.parametrize("width", [1440, 1280, 1024])
def test_the_whole_form_is_reachable_without_opening_anything(page, base_url, width):
    """The claim the redesign is built on: everything is on screen at load.

    Not "everything fits on one screen" — it does not, and the design says so.
    What must hold is that no field needs a click to *exist*, so a reader who
    scrolls has seen the whole form.

    `next-target_date` sits inside the «Kuupäev…» disclosure, and is counted
    here on the same rule: it is in the document at load, the four quick spans
    write into it without anybody opening anything, and the disclosure is a real
    `<details>` so it submits with scripting off (ADR 0052 §4).
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
    page.goto(f"{base_url}/osakond/")
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
