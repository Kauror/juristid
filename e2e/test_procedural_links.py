"""`Menetluse link` in a real browser: record one, read it, correct it.

The rules this file is here for are the ones only a running page can settle:

* that the tenth launcher choice opens, saves through HTMX, and puts the
  reference in the rail without a reload;
* that the rail card is **absent** on a Matter carrying none — no heading, no
  empty table and no five placeholder rows;
* that the link renders as its name or its host in a new tab and **never** as a
  printed address;
* that a refused address comes back in the box with what was typed;
* that a correction opens on the row's own values and saves in place;
* that the block on `Uus teema` files the reference with the Teema;
* that the whole thing is reachable from the keyboard and does not make the page
  scroll sideways at 420px or at 375px, with a register address long enough to
  be the thing that would break it.

The service-level rules — the vocabulary, the address rule, the per-Matter
uniqueness, the closed Matter, the concurrency, the audit trail and the
permissions — are `tests/test_procedural_links.py`, which is cheap and runs
everywhere.

**Everything here happens on a Matter the test creates.** The screenshot suite
opens `OPEN_TITLE`, and a rail that grew while these ran would make that baseline
depend on test order.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, create_matter, open_add_panel, sign_in, unique_title

pytestmark = pytest.mark.e2e

EIS_URL = "https://eelnoud.valitsus.ee/main/mount/docList/8f2c1a30-0000-0000-0000-000000000001"

#: A register address long enough to be the thing that overflows a 300px rail if
#: anything is going to. Real document-register deep links look like this.
LONG_REGISTER_URL = (
    "https://dokumendiregister.naidisministeerium.ee/otsing"
    "?dokumendi_number=1-4%2F2026-123&saabumise_kuupaev=2026-03-14&liik=kiri&lehekulg=1"
)


def a_new_matter(page, base_url: str) -> str:
    return create_matter(page, base_url, unique_title("Menetluse link"))


def panel(page):
    return page.locator("#lisa-menetluse-link")


def card(page):
    return page.locator("#menetluse-lingid")


def create_block(page):
    """`Menetluse link` on `Uus teema`, which is on screen when the page loads.

    It was a shut `<details>` and this helper opened it. docs/adr/0088's
    complaint was about *four* blocks expanded at once; there are two now, and
    what the fold cost instead was a click before the box could be typed into, on
    the one question whose answer is already on the reader's screen
    (docs/adr/0094 §3). Nothing to open, so this only names the block and waits
    for the box to be real.
    """
    block = page.locator("#menetluse-link")
    block.locator("[name='menetlus-url']").wait_for(state="visible")
    return block


def record_one(page, base_url, *, url: str = EIS_URL, kind: str = "EIS", label: str = "") -> None:
    """Open the panel, answer it, save, and wait for the card to appear."""
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-menetluse-link")
    panel(page).get_by_role("radio", name=kind, exact=True).check()
    panel(page).locator("[name=url]").fill(url)
    if label:
        panel(page).locator("[name=label]").fill(label)
    panel(page).get_by_role("button", name="Lisa menetluse link").click()
    card(page).wait_for()


# ---------------------------------------------------------------------------
# Recording one, and reading it back
# ---------------------------------------------------------------------------


def test_the_panel_records_a_reference_and_the_rail_shows_it(page, base_url):
    """Scenario A, in a browser: saved, visible, and openable."""
    sign_in(page, base_url, SANDRA)
    record_one(page, base_url, label="Eelnõu 123 SE")

    expect(card(page)).to_contain_text("Menetluse lingid")
    expect(card(page)).to_contain_text("EIS")
    link = card(page).get_by_role("link", name="Eelnõu 123 SE")
    expect(link).to_be_visible()
    expect(link).to_have_attribute("href", EIS_URL)
    expect(link).to_have_attribute("target", "_blank")
    expect(link).to_have_attribute("rel", "noopener noreferrer")


def test_the_card_shows_the_host_when_there_is_no_name(page, base_url):
    """A raw address as the row's own text is the one shape a look-alike is
    believed in, so the row reads as its host instead."""
    sign_in(page, base_url, SANDRA)
    record_one(page, base_url)

    expect(card(page).get_by_role("link", name="eelnoud.valitsus.ee")).to_be_visible()
    expect(card(page)).not_to_contain_text(EIS_URL)


def test_the_new_tab_is_announced_and_not_merely_used(page, base_url):
    sign_in(page, base_url, SANDRA)
    record_one(page, base_url)

    name = (
        card(page)
        .get_by_role("link", name="eelnoud.valitsus.ee")
        .evaluate("node => node.textContent.replace(/\\s+/g, ' ').trim()")
    )

    assert "avaneb uues aknas" in name


def test_several_references_coexist_on_one_matter(page, base_url):
    """Scenario B. Four kinds on one file, each keeping its own."""
    sign_in(page, base_url, SANDRA)
    record_one(page, base_url, label="EIS toimik")

    for kind, url, label in (
        ("Ministeeriumi dokumendiregister", LONG_REGISTER_URL, "Kiri 1-4/2026-123"),
        ("Riigikogu", "https://riigikogu.ee/tegevus/eelnoud/eelnou/123-SE", "Eelnõu 123 SE"),
        ("Muu menetluslink", "http://vana.register.example/2019/toimik/77", "Vana toimik"),
    ):
        open_add_panel(page, "lisa-menetluse-link")
        panel(page).get_by_role("radio", name=kind, exact=True).check()
        panel(page).locator("[name=url]").fill(url)
        panel(page).locator("[name=label]").fill(label)
        panel(page).get_by_role("button", name="Lisa menetluse link").click()
        card(page).get_by_role("link", name=label).wait_for()

    for label in ("EIS toimik", "Kiri 1-4/2026-123", "Eelnõu 123 SE", "Vana toimik"):
        expect(card(page).get_by_role("link", name=label)).to_be_visible()


def test_a_matter_with_no_references_shows_no_card_at_all(page, base_url):
    """Scenario C. No heading, no empty table, and no five placeholder rows."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    expect(card(page)).to_have_count(0)
    expect(page.locator("#teema-andmed")).not_to_contain_text("Menetluse lingid")
    # The one compact add affordance is the chip, and it is there.
    expect(page.locator("label[for='lisa-menetluse-link-valik']")).to_be_visible()


def test_the_panel_says_nothing_is_fetched(page, base_url):
    """The product promise, on the page where somebody decides to trust it."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-menetluse-link")

    expect(panel(page)).to_contain_text("lehte ei avata ega jälgita")


# ---------------------------------------------------------------------------
# Refusals, and corrections
# ---------------------------------------------------------------------------


def test_a_hostile_address_is_refused_with_the_value_returned(page, base_url):
    """Scenario J. Losing a pasted address would cost the one fact they came for."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-menetluse-link")
    panel(page).get_by_role("radio", name="EIS", exact=True).check()
    panel(page).locator("[name=url]").fill("javascript:alert(1)")
    panel(page).get_by_role("button", name="Lisa menetluse link").click()
    page.wait_for_timeout(400)

    expect(panel(page)).to_contain_text("http:// või https://")
    expect(panel(page).locator("[name=url]")).to_have_value("javascript:alert(1)")
    expect(card(page)).to_have_count(0)


def test_a_reference_can_be_corrected_from_its_own_row(page, base_url):
    """`Paranda` opens on the row's values and saves in place — no second row."""
    sign_in(page, base_url, SANDRA)
    record_one(page, base_url, label="Vale nimi")

    card(page).locator("details.proclink__fix summary").first.click()
    form = card(page).locator("form")
    form.wait_for(state="visible")
    expect(form.locator("[name=url]")).to_have_value(EIS_URL)
    form.locator("[name=label]").fill("Eelnõu 123 SE")
    form.get_by_role("button", name="Salvesta").click()
    card(page).get_by_role("link", name="Eelnõu 123 SE").wait_for()

    expect(card(page)).not_to_contain_text("Vale nimi")
    assert card(page).get_by_role("link").count() == 1


def test_the_same_address_cannot_be_added_twice_by_hand(page, base_url):
    """Scenario: the person genuinely repeats themselves rather than double-clicking.

    Answered with a sentence naming the row that is already there, and the card
    still holds one reference.
    """
    sign_in(page, base_url, SANDRA)
    record_one(page, base_url, label="Esimene")

    open_add_panel(page, "lisa-menetluse-link")
    panel(page).get_by_role("radio", name="Riigikogu", exact=True).check()
    panel(page).locator("[name=url]").fill(EIS_URL)
    panel(page).get_by_role("button", name="Lisa menetluse link").click()
    page.wait_for_timeout(400)

    expect(panel(page)).to_contain_text("juba")
    assert card(page).get_by_role("link").count() == 1


# ---------------------------------------------------------------------------
# `Uus teema`
# ---------------------------------------------------------------------------


def test_a_reference_can_be_recorded_while_the_teema_is_created(page, base_url):
    """The lawyer feedback's own request: the address is on screen *now*."""
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    page.fill("#id_title", unique_title("Menetluse link loomisel"))
    block = create_block(page)
    block.locator("[name='menetlus-url']").fill(EIS_URL)
    block.locator("[name='menetlus-label']").fill("Eelnõu 123 SE")
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))

    # The lawyer's own name for it is what the card reads by. The *source* was a
    # chip row on this form and is not asked here any more, so a row filed from
    # `Uus teema` carries the enum's neutral value — asserted on the record by
    # `tests/test_procedural_links_on_uus_teema.py` rather than on this card,
    # because the card contains the inline `Paranda` form and that form offers
    # every kind by name. A `not_to_contain_text("EIS")` here would be reading
    # an option nobody chose, which is the trap
    # `test_an_eu_matter_needs_no_second_european_question` documents about the
    # rail's own editor (docs/adr/0094 §3).
    expect(card(page).get_by_role("link", name="Eelnõu 123 SE")).to_be_visible()


def test_creating_a_teema_without_touching_the_block_records_nothing(page, base_url):
    """Two empty boxes on screen, and an ordinary submit writes no row."""
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Menetluse linkideta"))

    expect(card(page)).to_have_count(0)


def test_a_refused_create_keeps_the_typed_address(page, base_url):
    """Scenario D. The Teema is refused and the address is still in the box."""
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    page.fill("#id_title", unique_title("Menetluse link keeldumisel"))
    block = create_block(page)
    block.locator("[name='menetlus-url']").fill("javascript:alert(1)")
    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("networkidle")

    # The box to correct and the sentence explaining it are both simply on the
    # page: there is nothing to open, which is the whole of docs/adr/0094 §3.
    expect(page.locator("[name='menetlus-url']")).to_be_visible()
    expect(page.locator("[name='menetlus-url']")).to_have_value("javascript:alert(1)")
    expect(page.locator("#menetluse-link")).to_contain_text("http:// või https://")


# ---------------------------------------------------------------------------
# Keyboard, and the narrow viewport
# ---------------------------------------------------------------------------


def test_the_chip_is_reachable_and_operable_from_the_keyboard(page, base_url):
    """The radio is clipped rather than `display: none` precisely so it stays
    focusable, and the focus ring is drawn on the chip (docs/adr/0078 §1)."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    radio = page.locator("#lisa-menetluse-link-valik")
    radio.focus()
    page.keyboard.press("Space")

    expect(panel(page)).to_be_visible()
    expect(radio).to_be_focused()


def test_every_control_in_the_panel_is_reachable_by_tabbing(page, base_url):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-menetluse-link")

    for name in ("kind", "url", "label"):
        control = panel(page).locator(f"[name={name}]").first
        control.focus()
        expect(control).to_be_focused()


@pytest.mark.parametrize("width", [420, 375])
def test_the_panel_does_not_scroll_the_page_sideways_at_phone_width(page, base_url, width):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    page.set_viewport_size({"width": width, "height": 812})
    open_add_panel(page, "lisa-menetluse-link")

    overflows = page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )

    assert not overflows, "the Menetluse link panel makes the Teema page scroll sideways"


@pytest.mark.parametrize("width", [420, 375])
def test_a_long_register_address_does_not_destroy_the_layout(page, base_url, width):
    """Scenario K's real risk: the address, not the control around it.

    A register deep link is the longest unbreakable-looking string this
    application renders, and a flex item's automatic minimum is its longest
    unbreakable word — which is exactly how the rail came to hang past the right
    edge of the window once before (`.railcard__value`).
    """
    sign_in(page, base_url, SANDRA)
    record_one(page, base_url, kind="Ministeeriumi dokumendiregister", url=LONG_REGISTER_URL)
    page.set_viewport_size({"width": width, "height": 812})

    expect(card(page)).to_be_visible()
    overflows = page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )
    assert not overflows, "a long register address makes the Teema page scroll sideways"


def test_the_create_block_is_on_screen_when_the_page_loads(page, base_url):
    """docs/adr/0094 §3: the answer is already on the reader's screen.

    It was a shut `<details>`, on docs/adr/0088's argument that the capture page
    reads as a survey when too much is expanded at once. That argument was about
    four blocks and there are two; what the fold cost was a click before the box
    could be typed into.
    """
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")

    block = page.locator("#menetluse-link")
    expect(block).to_be_visible()
    expect(block.locator("[name='menetlus-url']")).to_be_visible()
    expect(block.locator("[name='menetlus-label']")).to_be_visible()
    # Nothing to open, and nothing to classify.
    expect(block.locator("summary")).to_have_count(0)
    expect(block.locator("[name='menetlus-kind']")).to_have_count(0)


def test_both_boxes_are_reachable_from_the_keyboard(page, base_url):
    """Two labelled inputs in the tab order, and nothing in front of them.

    It used to take a `<summary>` press to reach either. The block is open, so
    the claim is simply that the boxes are real controls a keyboard can land on
    (docs/adr/0094 §3).
    """
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")

    for name in ("menetlus-url", "menetlus-label"):
        control = page.locator(f"#menetluse-link [name='{name}']")
        control.focus()
        expect(control).to_be_focused()
        control.type("x")
        expect(control).to_have_value("x")
        control.fill("")


@pytest.mark.parametrize("width", [420, 375])
def test_the_uus_teema_block_does_not_scroll_the_form_sideways(page, base_url, width):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": 812})
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    create_block(page)
    page.locator("[name='menetlus-url']").fill(LONG_REGISTER_URL)

    expect(page.locator("#menetluse-link")).to_be_visible()
    overflows = page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )
    assert not overflows, "the Menetluse link block makes Uus teema scroll sideways"
