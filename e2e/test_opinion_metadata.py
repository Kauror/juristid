"""`Arvamuse märksõnad ja seosed` in a real browser.

The service-level rules — the governed vocabulary, the same-Matter invariant,
the audit trail, the visibility boundary, the absence of any backfill — are
`tests/test_opinion_marksonad_and_overview_links.py`, which is cheap and runs
everywhere. What only a running page can settle is here:

* that an opinion actually **has a door** to this surface, from the `⋯` behind
  its row on `Dokumendid` and from the draft row in the `Arvamused` block;
* that the page offers the governed keywords as tickable chips and saves several
  of them;
* that the Matter's own `Sildid` arrive **unticked** — the inheritance
  docs/adr/0093 §1 refuses, which on a screen would look like a helpful default;
* that a saved keyword and a saved write-up read back on the opinion's own row;
* that the reciprocal list appears under the planned write-up on the Teema page;
* that saving here changes nothing about the send.

**Everything happens on a Matter the test creates.** The screenshot suite opens
`OPEN_TITLE`, and an opinion that grew a keyword while these ran would make
`teema-dokumendid` depend on test order.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, create_matter, open_add_panel, sign_in, unique_title

pytestmark = pytest.mark.e2e

#: The two governed keywords `seed_e2e_data` puts in the world. Assigned to
#: nothing there, which is what makes ticking one here a real act.
KEYWORD = "Pakendid"
SECOND_KEYWORD = "Aktsiis"

#: What the opinion's own row offers behind its `⋯`, and what the draft row
#: offers beside it. One name for one surface.
METADATA_LINK = "Märksõnad ja seosed"

PLAN_BUTTON = "Lisa ülevaade / uudis"


def a_new_matter(page, base_url: str) -> str:
    return create_matter(page, base_url, unique_title("Arvamuse andmed"))


def open_opinion_block(page, matter_url: str):
    """`Dokumendid`, with the `Arvamused` accordion open."""
    page.goto(f"{matter_url.rstrip('/')}/dokumendid/")
    page.wait_for_load_state("networkidle")
    block = page.locator("#arvamuste-haldus")
    if not block.evaluate("node => node.open"):
        # `summary.accordion__head`, not `summary`: the block's body holds two
        # disclosures of its own, so a bare descendant selector resolves to
        # three elements and raises in strict mode.
        block.locator("summary.accordion__head").click()
    return block


def a_draft_opinion(page, base_url: str) -> str:
    """File a Matter, start a draft opinion on it, and return the Matter's URL.

    A draft rather than a send, because a draft needs no file: this suite is
    about the metadata surface, and the upload path has its own coverage in
    `e2e/test_lawyer_workflow_package.py`.
    """
    matter_url = a_new_matter(page, base_url)
    block = open_opinion_block(page, matter_url)
    block.locator("details.disclosure").filter(has_text="+ Uus arvamus").locator(
        "summary"
    ).click()
    form = block.locator("form[action*='/arvamused/teema/']")
    form.locator("[name='arvamus-title']").fill("Koja arvamus pakendiseaduse eelnõule")
    form.get_by_role("button", name="Loo arvamus").click()
    page.wait_for_load_state("networkidle")
    return matter_url


def open_metadata(page, base_url: str) -> str:
    """Reach the surface the way a lawyer does: through the draft's own row."""
    matter_url = a_draft_opinion(page, base_url)
    block = open_opinion_block(page, matter_url)
    block.get_by_role("link", name=METADATA_LINK).first.click()
    page.wait_for_load_state("networkidle")
    return matter_url


# ---------------------------------------------------------------------------
# The door, and the page behind it
# ---------------------------------------------------------------------------


def test_a_draft_opinion_offers_the_metadata_surface(page, base_url):
    """The draft's only door, because a draft with no file has no `⋯` menu."""
    sign_in(page, base_url, SANDRA)
    matter_url = a_draft_opinion(page, base_url)
    block = open_opinion_block(page, matter_url)

    expect(block.get_by_role("link", name=METADATA_LINK).first).to_be_visible()


def test_the_page_offers_both_controls(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_metadata(page, base_url)

    expect(page.get_by_role("heading", name="Arvamuse märksõnad ja seosed")).to_be_visible()
    expect(page.get_by_role("group", name="Märksõnad")).to_be_visible()
    expect(page.get_by_role("checkbox", name=KEYWORD, exact=True)).to_be_visible()


def test_the_matters_own_tags_are_not_preselected(page, base_url):
    """The decision, as a reader meets it: an empty form on a classified file.

    The Matter is given a `Silt` on `Muuda teemat` first, so the box that would
    be ticked by an inheriting implementation exists and is offered. It must be
    offered **unticked** — a default here is not a convenience, it is the
    application stating a classification nobody made (docs/adr/0093 §1).
    """
    sign_in(page, base_url, SANDRA)
    matter_url = a_draft_opinion(page, base_url)

    page.goto(f"{matter_url.rstrip('/')}/muuda/")
    page.wait_for_load_state("networkidle")
    page.get_by_role("checkbox", name=KEYWORD, exact=True).check()
    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_load_state("networkidle")

    block = open_opinion_block(page, matter_url)
    block.get_by_role("link", name=METADATA_LINK).first.click()
    page.wait_for_load_state("networkidle")

    expect(page.get_by_role("checkbox", name=KEYWORD, exact=True)).not_to_be_checked()
    expect(page.get_by_role("checkbox", name=SECOND_KEYWORD, exact=True)).not_to_be_checked()


def test_several_keywords_save_and_read_back(page, base_url):
    sign_in(page, base_url, SANDRA)
    matter_url = open_metadata(page, base_url)

    page.get_by_role("checkbox", name=KEYWORD, exact=True).check()
    page.get_by_role("checkbox", name=SECOND_KEYWORD, exact=True).check()
    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_load_state("networkidle")

    block = open_opinion_block(page, matter_url)
    block.get_by_role("link", name=METADATA_LINK).first.click()
    page.wait_for_load_state("networkidle")

    expect(page.get_by_role("checkbox", name=KEYWORD, exact=True)).to_be_checked()
    expect(page.get_by_role("checkbox", name=SECOND_KEYWORD, exact=True)).to_be_checked()


def test_a_correction_removes_only_what_was_unticked(page, base_url):
    sign_in(page, base_url, SANDRA)
    matter_url = open_metadata(page, base_url)

    page.get_by_role("checkbox", name=KEYWORD, exact=True).check()
    page.get_by_role("checkbox", name=SECOND_KEYWORD, exact=True).check()
    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_load_state("networkidle")

    block = open_opinion_block(page, matter_url)
    block.get_by_role("link", name=METADATA_LINK).first.click()
    page.wait_for_load_state("networkidle")
    page.get_by_role("checkbox", name=KEYWORD, exact=True).uncheck()
    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_load_state("networkidle")

    block = open_opinion_block(page, matter_url)
    block.get_by_role("link", name=METADATA_LINK).first.click()
    page.wait_for_load_state("networkidle")

    expect(page.get_by_role("checkbox", name=KEYWORD, exact=True)).not_to_be_checked()
    expect(page.get_by_role("checkbox", name=SECOND_KEYWORD, exact=True)).to_be_checked()


# ---------------------------------------------------------------------------
# The relation, and the reciprocal reading
# ---------------------------------------------------------------------------


def test_a_planned_write_up_can_be_linked_and_reads_back_under_it(page, base_url):
    """The whole round trip: plan a write-up, link the opinion, read the strip.

    This is the case ADR 0091 §8 deferred, and the only place its *effect* is
    visible: the planned `Ülevaade / uudis` on the Teema page naming the letter
    it is meant to cover.
    """
    sign_in(page, base_url, SANDRA)
    matter_url = a_draft_opinion(page, base_url)

    page.goto(matter_url)
    page.wait_for_load_state("networkidle")
    open_add_panel(page, "lisa-koduleht")
    page.locator("#lisa-koduleht").get_by_role("button", name=PLAN_BUTTON).click()
    page.locator("#kodulehe-ulevaated").wait_for(state="visible")

    block = open_opinion_block(page, matter_url)
    block.get_by_role("link", name=METADATA_LINK).first.click()
    page.wait_for_load_state("networkidle")

    overviews = page.get_by_role("group", name="Seotud ülevaated / uudised")
    expect(overviews).to_be_visible()
    overviews.get_by_role("checkbox").first.check()
    page.get_by_role("button", name="Salvesta").click()
    page.wait_for_load_state("networkidle")

    page.goto(matter_url)
    page.wait_for_load_state("networkidle")
    strip = page.locator("#kodulehe-ulevaated")
    expect(strip).to_contain_text("Seotud arvamused")
    expect(strip).to_contain_text("Koja arvamus pakendiseaduse eelnõule")


def test_a_matter_with_no_link_says_nothing_about_opinions(page, base_url):
    """No inference, as a reader meets it: an unlinked plan stays silent."""
    sign_in(page, base_url, SANDRA)
    matter_url = a_draft_opinion(page, base_url)

    page.goto(matter_url)
    page.wait_for_load_state("networkidle")
    open_add_panel(page, "lisa-koduleht")
    page.locator("#lisa-koduleht").get_by_role("button", name=PLAN_BUTTON).click()
    strip = page.locator("#kodulehe-ulevaated")
    strip.wait_for(state="visible")

    expect(strip).not_to_contain_text("Seotud arvamused")


def test_the_page_offers_nothing_that_could_change_the_send(page, base_url):
    """A metadata surface, not a second opinion editor (docs/adr/0093, ADR 0061).

    Asserted on the controls, because that is what stops somebody re-addressing
    a sent letter from here: there is no title box, no date box, no file input
    and no status control on the page at all.
    """
    sign_in(page, base_url, SANDRA)
    open_metadata(page, base_url)

    form = page.locator("form.createform")
    expect(form.locator("input[type=text]")).to_have_count(0)
    expect(form.locator("input[type=file]")).to_have_count(0)
    expect(form.locator("textarea")).to_have_count(0)
    expect(form.locator("select")).to_have_count(0)


def test_loobu_returns_to_the_file_list_without_saving(page, base_url):
    sign_in(page, base_url, SANDRA)
    matter_url = open_metadata(page, base_url)

    page.get_by_role("checkbox", name=KEYWORD, exact=True).check()
    page.get_by_role("link", name="Loobu").click()
    page.wait_for_load_state("networkidle")

    assert "/dokumendid/" in page.url

    block = open_opinion_block(page, matter_url)
    block.get_by_role("link", name=METADATA_LINK).first.click()
    page.wait_for_load_state("networkidle")

    expect(page.get_by_role("checkbox", name=KEYWORD, exact=True)).not_to_be_checked()
