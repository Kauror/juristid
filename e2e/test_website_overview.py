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


#: What the panel's primary action says. It named the *operation* rather than
#: the outcome until docs/adr/0083 — a fieldless form and a button saying
#: `Salvesta` read as a text area that had failed to load. It names no outcome
#: either: the one form reaches the plan and the published page alike, so it
#: cannot promise `planeeritud`.
PLAN_BUTTON = "Lisa ülevaade"

#: What a planned row offers next. `Avalda` named the lifecycle transition and
#: left the reader to discover it wanted two things (docs/adr/0083).
PUBLISH_DISCLOSURE = "Lisa link ja avaldamiskuupäev"


def plan_one(page, base_url: str) -> None:
    """`+ Kodulehe ülevaade` → the plan, on a Matter that has just been made."""
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-koduleht")
    page.locator("#lisa-koduleht").get_by_role("button", name=PLAN_BUTTON).click()
    strip(page).wait_for(state="visible")


def open_publish_form(page):
    """Open the publish disclosure on the first planned row."""
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
    # docs/adr/0083: two optional boxes, under their own legend, and a button
    # that says what the empty form does. What it must still not grow is a
    # title, a description or a file control.
    expect(panel.get_by_role("button", name=PLAN_BUTTON)).to_be_visible()
    expect(panel.locator("[name=url]")).to_be_visible()
    expect(panel.locator("[name=published_on]")).to_be_visible()
    expect(panel.get_by_text("Kui ülevaade on juba avaldatud")).to_be_visible()
    expect(panel.locator("textarea")).to_have_count(0)
    expect(panel.locator("input[type=file]")).to_have_count(0)


def test_publishing_moves_the_row_onto_the_chronology_as_a_labelled_link(page, base_url):
    sign_in(page, base_url, SANDRA)
    plan_one(page, base_url)

    disclosure = open_publish_form(page)
    disclosure.locator("[name=url]").fill(KODA_URL)
    disclosure.locator("[name=published_on]").fill("14.03.2026")
    disclosure.get_by_role("button", name="Salvesta avaldatuna").click()

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
    disclosure.get_by_role("button", name="Salvesta avaldatuna").click()
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
    disclosure.get_by_role("button", name="Salvesta avaldatuna").click()
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
    disclosure.get_by_role("button", name="Salvesta avaldatuna").click()
    chronology(page).get_by_role("link", name="Ava kodulehel").wait_for()

    chronology(page).get_by_role("button", name="Paranda link").click()
    region = chronology(page).locator(".uxtl__weblink")
    region.locator("[name=url]").wait_for(state="visible")
    expect(region.locator("[name=url]")).to_have_value(KODA_URL)
    region.locator("[name=url]").fill(f"{KODA_URL}-parandatud")
    # The *correction* form keeps `Salvesta`: it corrects an address already
    # recorded, and calling that «salvesta avaldatuna» would name a transition
    # this row has already made (docs/adr/0081 §5).
    region.get_by_role("button", name="Salvesta", exact=True).click()

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

    page.locator("#lisa-koduleht").get_by_role("button", name=PLAN_BUTTON).focus()
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


# ---------------------------------------------------------------------------
# docs/adr/0083 — the panel a lawyer meets first, and the published path
# ---------------------------------------------------------------------------


def test_the_panel_can_record_a_page_that_is_already_up(page, base_url):
    """The case the old panel could not express, in one act.

    Filling both boxes files the record straight as `Avaldatud`: no plan to
    publish afterwards, and no second control to find.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-koduleht")

    panel = page.locator("#lisa-koduleht")
    panel.locator("[name=url]").fill(KODA_URL)
    panel.locator("[name=published_on]").fill("14.03.2026")
    panel.get_by_role("button", name=PLAN_BUTTON).click()
    chronology(page).wait_for(state="visible")

    # Straight onto the chronology as a published overview, and no planned row
    # left behind on the strip.
    expect(chronology(page)).to_contain_text("Avaldatud")
    expect(strip(page)).to_have_count(0)
    expect(chronology(page).get_by_role("link", name="Ava kodulehel")).to_be_visible()


def test_half_a_publication_is_refused_and_keeps_what_was_typed(page, base_url):
    """An address without a date is a mistake, not a plan with a note attached.

    The refusal comes back through HTMX with the panel reopened and the address
    still in the box — losing it would cost the one fact they opened the panel
    to record.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-koduleht")

    panel = page.locator("#lisa-koduleht")
    panel.locator("[name=url]").fill(KODA_URL)
    panel.get_by_role("button", name=PLAN_BUTTON).click()
    page.wait_for_timeout(200)

    reopened = page.locator("#lisa-koduleht")
    expect(reopened).to_be_visible()
    expect(reopened.locator(".field__error").first).to_be_visible()
    assert reopened.locator("[name=url]").input_value() == KODA_URL
    # Nothing was filed.
    expect(strip(page)).to_have_count(0)


def test_a_look_alike_host_is_refused_from_the_panel_too(page, base_url):
    """The koda.ee boundary is the service's and reaches the new path unchanged."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-koduleht")

    panel = page.locator("#lisa-koduleht")
    panel.locator("[name=url]").fill("https://koda.ee.example.com/uudised/x")
    panel.locator("[name=published_on]").fill("14.03.2026")
    panel.get_by_role("button", name=PLAN_BUTTON).click()
    page.wait_for_timeout(200)

    expect(page.locator("#lisa-koduleht .field__error").first).to_be_visible()
    expect(strip(page)).to_have_count(0)


def test_a_planned_row_says_what_to_do_next(page, base_url):
    """`Avalda` named the transition; this names the action (docs/adr/0083)."""
    sign_in(page, base_url, SANDRA)
    plan_one(page, base_url)

    summary = strip(page).locator("details.webrow__publish summary").first
    expect(summary).to_have_text(PUBLISH_DISCLOSURE)


def test_the_panel_fits_a_phone_with_both_boxes(page, base_url):
    """Two controls where there were none, at 375px, without sideways scroll."""
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": 375, "height": 812})
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-koduleht")
    page.wait_for_timeout(120)

    panel = page.locator("#lisa-koduleht")
    expect(panel.locator("[name=url]")).to_be_visible()
    expect(panel.locator("[name=published_on]")).to_be_visible()
    assert not page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    ), "the + Kodulehe ülevaade panel makes the Teema page scroll sideways at 375px"
