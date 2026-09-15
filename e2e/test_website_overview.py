"""`Kodulehe ülevaade` in a real browser: plan one, publish it, and read it back.

The rules this file is here for are the ones only a running page can settle:

* that the eighth launcher choice opens, saves through HTMX, and puts the
  planned strip on the page without a reload;
* that publishing a plan moves it off the strip and onto the chronology in the
  same swap, as a labelled `Ava kodulehel` that opens in a new tab — never as a
  printed address;
* that a refused address comes back with what was typed still in the box;
* that the whole thing is reachable from the keyboard and does not make the page
  scroll sideways at phone width.

The service-level rules — the `koda.ee` boundary, the lifecycle, the closed
Matter, the audit trail — are `tests/test_website_overviews.py`, which is cheap
and runs everywhere.

**Everything here happens on a Matter the test creates.** The screenshot suite
opens `OPEN_TITLE`, and a chronology that grew while these ran would make that
baseline depend on test order.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, create_matter, open_add_panel, sign_in, unique_title

pytestmark = pytest.mark.e2e

KODA_URL = "https://koda.ee/uudised/e2e-ulevaade"


def a_new_matter(page, base_url: str) -> str:
    return create_matter(page, base_url, unique_title("Kodulehe ülevaade"))


def strip(page):
    return page.locator("#kodulehe-ulevaated")


def chronology(page):
    return page.locator("#ajalugu-loend")


def plan_one(page, base_url: str) -> None:
    """`+ Kodulehe ülevaade` → `Salvesta`, on a Matter that has just been made."""
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-koduleht")
    page.locator("#lisa-koduleht").get_by_role("button", name="Salvesta").click()
    strip(page).wait_for(state="visible")


def open_publish_form(page):
    """Open the `Avalda` disclosure on the first planned row."""
    disclosure = strip(page).locator("details.webrow__publish").first
    if not disclosure.evaluate("node => node.open"):
        disclosure.locator("summary").click()
    disclosure.locator("form").wait_for(state="visible")
    return disclosure


# ---------------------------------------------------------------------------
# Desktop: plan, publish, read
# ---------------------------------------------------------------------------


def test_a_matter_with_nothing_planned_shows_no_strip(page, base_url):
    """There are no permanently visible empty sections on this page."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    expect(strip(page)).to_have_count(0)


def test_the_eighth_choice_records_a_plan_without_a_reload(page, base_url):
    sign_in(page, base_url, SANDRA)
    plan_one(page, base_url)

    expect(strip(page)).to_contain_text("Ülevaade on plaanis, aga veel avaldamata.")
    # A plan is not a milestone: the chronology says nothing about it until
    # something actually happens (docs/adr/0081 §4).
    expect(chronology(page)).not_to_contain_text("Kodulehe ülevaade")


def test_the_panel_asks_for_no_address_and_no_date(page, base_url):
    """At the moment somebody decides this, the page does not exist yet — so
    there is one button and nothing to invent (docs/adr/0081 §1)."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-koduleht")

    panel = page.locator("#lisa-koduleht")
    expect(panel.locator("input[type=text], input[type=url]")).to_have_count(0)
    expect(panel.get_by_role("button", name="Salvesta")).to_be_visible()


def test_publishing_moves_the_row_onto_the_chronology_as_a_labelled_link(page, base_url):
    sign_in(page, base_url, SANDRA)
    plan_one(page, base_url)

    disclosure = open_publish_form(page)
    disclosure.locator("[name=url]").fill(KODA_URL)
    disclosure.locator("[name=published_on]").fill("14.03.2026")
    disclosure.get_by_role("button", name="Salvesta").click()

    # The plan is discharged, so the strip goes with it.
    expect(strip(page)).to_have_count(0)

    link = chronology(page).get_by_role("link", name="Ava kodulehel")
    expect(link).to_be_visible()
    expect(link).to_have_attribute("href", KODA_URL)
    expect(link).to_have_attribute("target", "_blank")
    expect(link).to_have_attribute("rel", "noopener noreferrer")
    # The address is where the link goes, never what the row says.
    expect(chronology(page)).not_to_contain_text(KODA_URL)
    expect(chronology(page)).to_contain_text("Avaldatud")


def test_the_new_tab_is_announced_and_not_merely_used(page, base_url):
    """A sighted reader sees the link; a screen-reader user is told it leaves
    the page. Read off the accessible name, not off the markup."""
    sign_in(page, base_url, SANDRA)
    plan_one(page, base_url)
    disclosure = open_publish_form(page)
    disclosure.locator("[name=url]").fill(KODA_URL)
    disclosure.locator("[name=published_on]").fill("14.03.2026")
    disclosure.get_by_role("button", name="Salvesta").click()
    chronology(page).get_by_role("link", name="Ava kodulehel").wait_for()

    name = (
        chronology(page)
        .get_by_role("link", name="Ava kodulehel")
        .evaluate("node => node.textContent.replace(/\\s+/g, ' ').trim()")
    )

    assert "avaneb uues aknas" in name


def test_a_refused_address_comes_back_with_what_was_typed(page, base_url):
    """`https://koda.ee.example.com/…` contains `koda.ee` and is somebody else's
    domain. The refusal arrives as an HTMX swap, the panel is still open, and
    nothing that was typed is gone (docs/adr/0081 §3)."""
    sign_in(page, base_url, SANDRA)
    plan_one(page, base_url)

    disclosure = open_publish_form(page)
    disclosure.locator("[name=url]").fill("https://koda.ee.example.com/uudised/x")
    disclosure.locator("[name=published_on]").fill("14.03.2026")
    disclosure.get_by_role("button", name="Salvesta").click()
    page.wait_for_timeout(400)

    reopened = strip(page).locator("details.webrow__publish").first
    expect(reopened.locator("[name=url]")).to_have_value("https://koda.ee.example.com/uudised/x")
    expect(reopened.locator("[name=published_on]")).to_have_value("14.03.2026")
    expect(strip(page)).to_contain_text("koda.ee")
    # And the plan is still a plan.
    expect(strip(page)).to_contain_text("Ülevaade on plaanis, aga veel avaldamata.")


def test_cancelling_a_plan_leaves_it_on_the_chronology(page, base_url):
    """Nothing is deleted when a plan changes."""
    sign_in(page, base_url, SANDRA)
    plan_one(page, base_url)

    strip(page).get_by_role("button", name="Tühista").click()

    expect(strip(page)).to_have_count(0)
    expect(chronology(page)).to_contain_text("Kodulehe ülevaade")
    expect(chronology(page)).to_contain_text("Tühistatud")


def test_a_published_address_can_be_corrected_from_its_own_row(page, base_url):
    """The row around the form never moves: `Paranda link` swaps the link region
    and nothing else (docs/adr/0081 §5)."""
    sign_in(page, base_url, SANDRA)
    plan_one(page, base_url)
    disclosure = open_publish_form(page)
    disclosure.locator("[name=url]").fill(KODA_URL)
    disclosure.locator("[name=published_on]").fill("14.03.2026")
    disclosure.get_by_role("button", name="Salvesta").click()
    chronology(page).get_by_role("link", name="Ava kodulehel").wait_for()

    chronology(page).get_by_role("button", name="Paranda link").click()
    region = chronology(page).locator(".uxtl__weblink")
    region.locator("[name=url]").wait_for(state="visible")
    expect(region.locator("[name=url]")).to_have_value(KODA_URL)
    region.locator("[name=url]").fill(f"{KODA_URL}-parandatud")
    region.get_by_role("button", name="Salvesta").click()

    link = chronology(page).get_by_role("link", name="Ava kodulehel")
    expect(link).to_have_attribute("href", f"{KODA_URL}-parandatud")


# ---------------------------------------------------------------------------
# Keyboard
# ---------------------------------------------------------------------------


def test_the_chip_and_the_publish_disclosure_work_from_the_keyboard(page, base_url):
    """Every control here has to be reachable without a mouse.

    The launcher radio is clipped rather than `display: none` precisely so it
    stays focusable, and the disclosure is a native `<summary>` — so both are
    operated here the way a keyboard user operates them, rather than clicked.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)

    page.locator("#lisa-koduleht-valik").focus()
    page.keyboard.press("Space")
    expect(page.locator("#lisa-koduleht")).to_be_visible()
    assert page.locator("#lisa-koduleht-valik").is_checked()

    page.locator("#lisa-koduleht").get_by_role("button", name="Salvesta").focus()
    page.keyboard.press("Enter")
    strip(page).wait_for(state="visible")

    summary = strip(page).locator("details.webrow__publish summary").first
    summary.focus()
    page.keyboard.press("Enter")
    expect(strip(page).locator("[name=url]").first).to_be_visible()


# ---------------------------------------------------------------------------
# Narrow
# ---------------------------------------------------------------------------


def test_the_strip_and_its_form_fit_a_phone(page, base_url):
    """At 375px the row wraps and the form stacks. What it must not do is make
    the Teema page scroll sideways."""
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": 375, "height": 812})
    plan_one(page, base_url)
    open_publish_form(page)
    page.wait_for_timeout(120)

    assert not page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    ), "the Kodulehe ülevaated strip makes the Teema page scroll sideways at 375px"
    expect(strip(page).locator("[name=url]").first).to_be_visible()
    expect(strip(page).get_by_role("button", name="Tühista")).to_be_visible()
