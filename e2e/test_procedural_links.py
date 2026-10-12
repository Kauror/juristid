"""`Menetluse link` in a real browser: record one, read it, correct it.

The rules this file is here for are the ones only a running page can settle:

* that `Muuda teemat` records a reference and the rail shows it;
* that the rail card holds one quiet `+ Lisa` line on a Matter carrying none —
  no empty table and no five placeholder rows;
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

**The `LISA TEEMALE` panel is gone.** `+ Menetluse link` was the launcher's
tenth chip until 2026-09-20; an address is a fact *about* a Matter rather than
something that happened to it, so the question moved to the two Teema forms and
the chip and its route went with it (docs/adr/0097 §5). The scenarios below are
unchanged in what they claim and are driven through `Muuda teemat`, which is the
surface that now answers them — including the source vocabulary, which this
surface deliberately does not ask and which `Paranda` on a recorded row still
does.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, create_matter, sign_in, unique_title

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


def edit_block(page):
    """`Menetluse link` on `Muuda teemat` — the same two boxes `Uus teema` draws."""
    return page.locator("#menetluse-link")


def open_edit(page, base_url: str) -> None:
    """Go from the Teema page to its `Muuda teemat`, and wait for the block."""
    page.get_by_role("link", name="Muuda teemat").first.click()
    page.wait_for_load_state("load")
    edit_block(page).locator("[name='menetlus-url']").wait_for(state="visible")


def card(page):
    return page.locator("#menetluse-lingid")


def save_link(page, *, url: str) -> None:
    """Answer the block on an open `Muuda teemat` and save the page.

    The address only: `Nimetus` is not asked here since docs/adr/0130 §3. A
    name is still given, where a test needs one, through the row's own
    `Paranda` (`name_the_link`).
    """
    edit_block(page).locator("[name='menetlus-url']").fill(url)
    page.get_by_role("button", name="Salvesta").first.click()
    page.wait_for_load_state("load")


def record_one(page, base_url, *, url: str = EIS_URL) -> None:
    """File a Matter, correct it with an address, and wait for the card."""
    a_new_matter(page, base_url)
    open_edit(page, base_url)
    save_link(page, url=url)
    card(page).wait_for()


def name_the_link(page, name: str) -> None:
    """Give the card's one link a name through its `Paranda` — the one place a
    stored name is still written (docs/adr/0130 §3)."""
    card(page).locator("details.proclink__fix summary").first.click()
    form = card(page).locator("form")
    form.wait_for(state="visible")
    form.locator("[name=label]").fill(name)
    form.get_by_role("button", name="Salvesta").click()
    card(page).get_by_role("link", name=name).wait_for()


# ---------------------------------------------------------------------------
# Recording one, and reading it back
# ---------------------------------------------------------------------------


def test_the_panel_records_a_reference_and_the_rail_shows_it(page, base_url):
    """Scenario A, in a browser: saved, visible, and openable."""
    sign_in(page, base_url, SANDRA)
    record_one(page, base_url)
    name_the_link(page, "Eelnõu 123 SE")

    expect(card(page)).to_contain_text("Menetluse link")
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


def test_a_matter_with_no_references_shows_one_quiet_add_line(page, base_url):
    """Scenario C. No empty table and no five placeholder rows — one line.

    The card used to be absent entirely, because the add affordance was the
    launcher chip. That chip is gone, and an address recorded nowhere with no
    visible way to record one is a capability that has quietly left the product
    (docs/adr/0097 §5).
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    expect(card(page).get_by_role("link", name="Lisa menetluse link")).to_be_visible()
    expect(card(page).get_by_role("link", name="eelnoud.valitsus.ee")).to_have_count(0)
    expect(page.locator("label[for='lisa-menetluse-link-valik']")).to_have_count(0)


def test_the_add_line_lands_on_the_block_that_asks(page, base_url):
    """`+ Lisa` is a link to the question, not a second control asking it."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    card(page).get_by_role("link", name="Lisa menetluse link").click()
    page.wait_for_load_state("load")

    expect(edit_block(page).locator("[name='menetlus-url']")).to_be_visible()


# ---------------------------------------------------------------------------
# Refusals, and corrections
# ---------------------------------------------------------------------------


def test_a_hostile_address_is_refused_with_the_value_returned(page, base_url):
    """Scenario J. Losing a pasted address would cost the one fact they came for."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_edit(page, base_url)
    save_link(page, url="javascript:alert(1)")

    expect(edit_block(page)).to_contain_text("http:// või https://")
    expect(edit_block(page).locator("[name='menetlus-url']")).to_have_value("javascript:alert(1)")


def test_a_reference_can_be_corrected_from_its_own_row(page, base_url):
    """`Paranda` opens on the row's values and saves in place — no second row."""
    sign_in(page, base_url, SANDRA)
    record_one(page, base_url)
    name_the_link(page, "Vale nimi")

    card(page).locator("details.proclink__fix summary").first.click()
    form = card(page).locator("form")
    form.wait_for(state="visible")
    expect(form.locator("[name=url]")).to_have_value(EIS_URL)
    form.locator("[name=label]").fill("Eelnõu 123 SE")
    form.get_by_role("button", name="Salvesta").click()
    card(page).get_by_role("link", name="Eelnõu 123 SE").wait_for()

    expect(card(page)).not_to_contain_text("Vale nimi")
    assert card(page).get_by_role("link").count() == 1


def test_an_emptied_address_is_refused_rather_than_silently_ignored(page, base_url):
    """There is no deletion of a link, so emptying the box is not one either.

    Ignoring it would leave the page saying the link was gone while the record
    still held it, which is worse than either answer (docs/adr/0084 §8).
    """
    sign_in(page, base_url, SANDRA)
    record_one(page, base_url)
    open_edit(page, base_url)
    save_link(page, url="")

    expect(edit_block(page)).to_contain_text("vajab veebiaadressi")


# ---------------------------------------------------------------------------
# `Uus teema`
# ---------------------------------------------------------------------------


def test_uus_teema_asks_no_menetluse_link_and_the_teema_page_takes_one(page, base_url):
    """**Superseded on 2026-10-12** (docs/adr/0130 amendment): this test filed
    a link from `Uus teema`. The owner removed the question from creation; the
    link is added on the Teema page, where it always could be."""
    sign_in(page, base_url, SANDRA)
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")
    expect(page.locator("[name='menetlus-url']")).to_have_count(0)
    expect(page.get_by_label("Menetluse link")).to_have_count(0)

    create_matter(page, base_url, unique_title("Menetluse link hiljem"))
    expect(card(page).get_by_role("link", name="Lisa menetluse link")).to_be_visible()


def test_creating_a_teema_without_touching_the_block_records_nothing(page, base_url):
    """Two empty boxes on screen, and an ordinary submit writes no row.

    The card renders — it holds the `+ Lisa` line now — so «no row» is read
    off the rows rather than off the card's presence (docs/adr/0097 §5).
    """
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Menetluse linkideta"))

    expect(card(page).get_by_role("link", name="Lisa menetluse link")).to_be_visible()
    expect(card(page).locator(".proclink")).to_have_count(0)


# ---------------------------------------------------------------------------
# Keyboard, and the narrow viewport
# ---------------------------------------------------------------------------


def test_the_add_line_is_reachable_and_operable_from_the_keyboard(page, base_url):
    """The rail's `+ Lisa` is an ordinary link, so Enter is what opens it.

    It replaces the launcher chip — a clipped radio whose focus ring was drawn
    on its label — and an anchor needs none of that arrangement to be
    focusable, which is the point of it being one (docs/adr/0097 §5).
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    add = card(page).get_by_role("link", name="Lisa menetluse link")
    add.focus()
    expect(add).to_be_focused()
    page.keyboard.press("Enter")
    page.wait_for_load_state("load")

    expect(edit_block(page).locator("[name='menetlus-url']")).to_be_visible()


def test_every_control_in_the_block_is_reachable_by_tabbing(page, base_url):
    """One box — neither `kind` nor `Nimetus` is asked on this surface."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_edit(page, base_url)

    control = edit_block(page).locator("[name='menetlus-url']").first
    control.focus()
    expect(control).to_be_focused()

    expect(edit_block(page).locator("[name='menetlus-kind']")).to_have_count(0)
    expect(edit_block(page).locator("[name='menetlus-label']")).to_have_count(0)


@pytest.mark.parametrize("width", [420, 375])
def test_the_block_does_not_scroll_the_page_sideways_at_phone_width(page, base_url, width):
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_edit(page, base_url)
    page.set_viewport_size({"width": width, "height": 812})

    overflows = page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )

    assert not overflows, "the Menetluse link block makes Muuda teemat scroll sideways"


@pytest.mark.parametrize("width", [420, 375])
def test_a_long_register_address_does_not_destroy_the_layout(page, base_url, width):
    """Scenario K's real risk: the address, not the control around it.

    A register deep link is the longest unbreakable-looking string this
    application renders, and a flex item's automatic minimum is its longest
    unbreakable word — which is exactly how the rail came to hang past the right
    edge of the window once before (`.railcard__value`).
    """
    sign_in(page, base_url, SANDRA)
    record_one(page, base_url, url=LONG_REGISTER_URL)
    page.set_viewport_size({"width": width, "height": 812})

    expect(card(page)).to_be_visible()
    overflows = page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )
    assert not overflows, "a long register address makes the Teema page scroll sideways"
