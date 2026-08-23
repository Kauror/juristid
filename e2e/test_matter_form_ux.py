"""Uus teema, read the way a lawyer reads it.

The database-level guarantees — cardinality, derived date meanings, the
canonical record a POST produces — are asserted in
`tests/test_matter_form_controls.py`. What only a browser shows is whether the
page *reads* as those guarantees: whether ticking a second Hetkeseis clears the
first, whether the date label follows the chosen kind, and whether the two
deadlines look like the two different things they are.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, sign_in

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


@pytest.mark.parametrize("field", ["stage", "track"])
def test_the_procedural_fields_are_visible_choices_not_dropdowns(page, base_url, field):
    """For a department of four, a select is a click spent finding out what the
    options even are."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)
    open_details(page, "Täpsusta teema andmeid")

    expect(page.locator(f'select[name="{field}"]')).to_have_count(0)
    expect(page.locator(f'input[type="radio"][name="{field}"]').first).to_be_visible()


@pytest.mark.parametrize("field", ["stage", "track"])
def test_choosing_a_second_value_replaces_the_first(page, base_url, screenshots, field):
    """The cardinality promise, seen rather than inferred.

    `Matter.stage` and `Matter.track` hold one value each. A control that let
    two stay ticked would be promising something the model cannot keep — and a
    checkmark on a card is decoration on a radio, not a second checkbox.
    """
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)
    open_details(page, "Täpsusta teema andmeid")

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


def test_the_three_kinds_are_cards_a_lawyer_can_tell_apart(page, base_url, screenshots):
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)
    open_details(page, "Järgmine tegevus")

    block = page.locator("#jargmine-liik")
    expect(block).to_be_visible()
    expect(block).to_contain_text("Mul endal tuleb midagi teha")
    expect(block).to_contain_text("Ootan infot, vastust, eelnõud või muud arengut")
    expect(block).to_contain_text("Vaatan teema hiljem uuesti üle")
    screenshots(page, "jargmine-tegevus")


def test_only_one_kind_can_be_active(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)
    open_details(page, "Järgmine tegevus")

    wait = page.locator('input[name="next-kind"][value="WAIT"]')
    monitor = page.locator('input[name="next-kind"][value="MONITOR"]')
    wait.check()
    expect(wait).to_be_checked()
    monitor.check()
    expect(monitor).to_be_checked()
    expect(wait).not_to_be_checked()


@pytest.mark.parametrize(
    ("value", "wording"),
    [
        ("DO", "Tähtaeg"),
        ("WAIT", "Millal võiks arengut oodata?"),
        ("MONITOR", "Millal vaatan uuesti üle?"),
    ],
)
def test_the_date_label_says_what_the_date_means_for_the_chosen_kind(
    page, base_url, value, wording
):
    """The question the technical "Kuupäeva tähendus" dropdown was asking, in
    the vocabulary of the work rather than of the database."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)
    open_details(page, "Järgmine tegevus")

    page.locator(f'input[name="next-kind"][value="{value}"]').check()
    expect(page.locator("[data-datelabel-for]").first).to_have_text(wording)


def test_the_technical_date_meaning_is_available_but_not_in_the_way(page, base_url):
    """Not deleted: the model genuinely permits pairs the derivation does not
    produce, and the register's own parser records some of them."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)
    open_details(page, "Järgmine tegevus")

    field = page.locator('select[name="next-date_semantics"]')
    expect(field).to_have_count(1)
    expect(field).to_be_hidden()

    open_details(page, "Täpsusta, mida kuupäev tähendab")
    expect(field).to_be_visible()


def test_the_two_deadlines_are_told_apart_in_words(page, base_url):
    """Arvamuse tähtaeg is when this opinion must go out; Järgmine tegevus is
    what happens next with the file. They are not two versions of one date."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)
    open_details(page, "Järgmine tegevus")

    expect(page.locator(".disclosure", has_text="Arvamuse tähtaeg on eraldi").first).to_be_visible()


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

    expect(page.locator(".nextaction")).to_contain_text("Järgmine samm on määramata")


def test_a_next_action_created_here_takes_the_chosen_owner(page, base_url):
    """Nobody should have to name the same colleague twice on one form."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)

    page.fill("#id_title", "Vastutaja pärandub järgmisele sammule")
    page.locator('input[name="owner"]').first.check()
    open_details(page, "Järgmine tegevus")
    page.fill("#id_next-text", "Jälgi menetluse käiku")
    page.locator('input[name="next-kind"][value="MONITOR"]').check()
    page.fill("#id_next-target_date", "1.9.2026")
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    responsible = page.locator(".nextaction__responsible")
    expect(responsible).to_be_visible()
    assert responsible.inner_text().strip(), "the next action has nobody responsible for it"
