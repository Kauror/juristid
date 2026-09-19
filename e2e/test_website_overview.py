"""`Ülevaade / uudis` in a real browser: plan one, publish it, and read it back.

The rules this file is here for are the ones only a running page can settle:

* that the eighth launcher choice opens, saves through HTMX, and puts the
  planned strip on the page without a reload;
* that publishing a plan moves it off the strip and onto the chronology in the
  same swap, as a labelled `Ava ülevaade või uudis` that opens in a new tab —
  never as a printed address;
* that a refused address comes back with what was typed still in the box;
* that nothing *reacts* to a paste — the island ADR 0085 §3 added is still gone
  (docs/adr/0089 §8) — while the box itself opens on today, and a cleared box
  still files a publication reading «Kuupäev teadmata» (docs/adr/0095 §5);
* that a form nobody touched is **refused** rather than quietly filing a plan,
  and that a stored plan still publishes and cancels from the strip;
* that the whole thing is reachable from the keyboard and does not make the page
  scroll sideways at phone width.

The service-level rules — the address rule, the lifecycle, the closed Matter,
the audit trail — are `tests/test_website_overviews.py` and
`tests/test_overview_news_publication.py`, which are cheap and run everywhere.

**Almost everything here happens on a Matter the test creates.** The screenshot
suite opens `OPEN_TITLE`, and a chronology that grew while these ran would make
that baseline depend on test order.

The planned-row lifecycle still gets a Matter of its own, but its plan is
written by `plan_website_overview` in a subprocess rather than by clicking —
docs/adr/0095 §5 retired the control that made one. See `plan_one`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, create_matter, open_add_panel, sign_in, unique_title

pytestmark = pytest.mark.e2e

KODA_URL = "https://koda.ee/uudised/e2e-ulevaade"


def a_new_matter(page, base_url: str) -> str:
    return create_matter(page, base_url, unique_title("Ülevaade uudis"))


def strip(page):
    return page.locator("#kodulehe-ulevaated")


def chronology(page):
    return page.locator("#ajalugu-loend")


#: What the panel's primary action says. It named the *operation* rather than
#: the outcome until docs/adr/0083 — a fieldless form and a button saying
#: `Salvesta` read as a text area that had failed to load. It names no outcome
#: either: the one form reaches the plan and the published page alike, so it
#: cannot promise `planeeritud`. `/ uudis` since docs/adr/0085 §1, because the
#: write-up is as often a news item as a Koda overview.
PLAN_BUTTON = "Lisa ülevaade / uudis"

#: What a planned row offers next. `Avalda` named the lifecycle transition and
#: left the reader to discover it wanted two things (docs/adr/0083).
PUBLISH_DISCLOSURE = "Lisa link ja avaldamiskuupäev"


def plan_one(page, base_url: str) -> None:
    """A Matter of this test's own, carrying one planned `Ülevaade / uudis`.

    **The plan is written by the service, not by clicking**, because
    docs/adr/0095 §5 retired the control that made one: `+ Ülevaade / uudis`
    records a page that exists, and an empty save is refused rather than quietly
    filing a plan. `Plaanis` and `Tühistatud` are untouched in the domain, every
    stored row still reads, publishes and cancels — which is exactly what the
    tests below measure — so the suite still needs a file that has one.

    **A subprocess, and a fresh Matter each time.** The same shape
    `e2e/test_document_content.py` uses to drain the extraction queue: a
    separate process, its own connection, the real server's settings. Seeding a
    shared planned row instead would have been cheaper and wrong twice over —
    the first test to publish or cancel it would empty the fixture for every
    later one, and a new row in `seed_e2e_data` moves the register that the
    screenshot suite photographs.
    """
    matter_url = a_new_matter(page, base_url)
    matter_id = matter_url.rstrip("/").rsplit("/", 1)[-1]
    _plan_through_the_service(matter_id)
    page.goto(matter_url)
    page.wait_for_load_state("networkidle")
    strip(page).wait_for(state="visible")


#: `plan_website_overview` on the Matter named by `E2E_PLAN_MATTER`.
#:
#: A literal, with the Matter's id carried in the environment rather than
#: interpolated into it. That is what keeps the `subprocess.run` below a call
#: with no constructed arguments — the shape `run_worker` already has, and the
#: one the linter is right to insist on for anything that spawns a process.
_PLAN_SCRIPT = (
    "import os;"
    "from app.accounts.models import User;"
    "from app.matters.models import Matter;"
    "from app.matters.services import plan_website_overview;"
    "m = Matter.objects.get(pk=os.environ['E2E_PLAN_MATTER']);"
    "plan_website_overview(matter=m, actor=m.owner or User.objects.first())"
)


def _plan_through_the_service(matter_id: str) -> None:
    """`plan_website_overview` on one Matter, in the server's own environment.

    `DJANGO_SETTINGS_MODULE` is forced for the reason `run_worker` gives at
    length: pytest sets `config.test_settings` for itself, a child would inherit
    it, and those settings mint their own storage roots — so the child would
    write into a world the running server cannot see.
    """
    result = subprocess.run(  # noqa: S603
        [sys.executable, "manage.py", "shell", "-c", _PLAN_SCRIPT],
        cwd=Path(__file__).resolve().parents[1],
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "E2E_PLAN_MATTER": matter_id,
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    report = "\n".join(["stdout:", result.stdout, "stderr:", result.stderr])
    assert result.returncode == 0, report


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


def test_a_stored_plan_reads_on_the_file(page, base_url):
    """`Plaanis` is untouched in the domain, and a file that has one says so."""
    sign_in(page, base_url, SANDRA)
    plan_one(page, base_url)

    expect(strip(page)).to_contain_text("Ülevaade või uudis on plaanis, aga veel avaldamata.")
    # A plan is not a milestone: the chronology says nothing about it until
    # something actually happens (docs/adr/0081 §4).
    expect(chronology(page)).not_to_contain_text("Ülevaade / uudis")


def test_the_panel_asks_a_day_and_an_address_and_nothing_else(page, base_url):
    """Two boxes, and what docs/adr/0081 §2 still refuses: a title, a body, a file."""
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
    # docs/adr/0095 §5: the legend that explained a conditional path went with
    # the conditional, and the date box opens on today.
    expect(panel.get_by_text("Kui ülevaade või uudis on juba avaldatud")).to_have_count(0)
    expect(panel.locator("[name=published_on]")).not_to_have_value("")
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

    link = chronology(page).get_by_role("link", name="Ava ülevaade või uudis")
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
    chronology(page).get_by_role("link", name="Ava ülevaade või uudis").wait_for()

    name = (
        chronology(page)
        .get_by_role("link", name="Ava ülevaade või uudis")
        .evaluate("node => node.textContent.replace(/\\s+/g, ' ').trim()")
    )

    assert "avaneb uues aknas" in name


def test_a_refused_address_comes_back_with_what_was_typed(page, base_url):
    """`https://koda.ee@example.com/…` puts the Chamber's name in the *userinfo*,
    which every browser ignores when resolving. The refusal arrives as an HTMX
    swap, the panel is still open, and nothing that was typed is gone
    (docs/adr/0081 §3, kept by docs/adr/0085 §2)."""
    sign_in(page, base_url, SANDRA)
    plan_one(page, base_url)

    disclosure = open_publish_form(page)
    disclosure.locator("[name=url]").fill("https://koda.ee@example.com/uudised/x")
    disclosure.locator("[name=published_on]").fill("14.03.2026")
    disclosure.get_by_role("button", name="Salvesta avaldatuna").click()
    page.wait_for_timeout(400)

    reopened = strip(page).locator("details.webrow__publish").first
    expect(reopened.locator("[name=url]")).to_have_value("https://koda.ee@example.com/uudised/x")
    expect(reopened.locator("[name=published_on]")).to_have_value("14.03.2026")
    expect(strip(page)).to_contain_text("kasutajanime ega parooli")
    # And the plan is still a plan.
    expect(strip(page)).to_contain_text("Ülevaade või uudis on plaanis, aga veel avaldamata.")


def test_cancelling_a_plan_leaves_it_on_the_chronology(page, base_url):
    """Nothing is deleted when a plan changes."""
    sign_in(page, base_url, SANDRA)
    plan_one(page, base_url)

    strip(page).get_by_role("button", name="Tühista").click()

    expect(strip(page)).to_have_count(0)
    expect(chronology(page)).to_contain_text("Ülevaade / uudis")
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
    chronology(page).get_by_role("link", name="Ava ülevaade või uudis").wait_for()

    chronology(page).get_by_role("button", name="Paranda link").click()
    region = chronology(page).locator(".uxtl__weblink")
    region.locator("[name=url]").wait_for(state="visible")
    expect(region.locator("[name=url]")).to_have_value(KODA_URL)
    region.locator("[name=url]").fill(f"{KODA_URL}-parandatud")
    # The *correction* form keeps `Salvesta`: it corrects an address already
    # recorded, and calling that «salvesta avaldatuna» would name a transition
    # this row has already made (docs/adr/0081 §5).
    region.get_by_role("button", name="Salvesta", exact=True).click()

    link = chronology(page).get_by_role("link", name="Ava ülevaade või uudis")
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

    # Filled from the keyboard and submitted from the keyboard. It used to press
    # Enter on an untouched panel and read the plan that appeared; since
    # docs/adr/0095 §5 an empty save is refused, so the address is typed — which
    # is what a keyboard user does anyway, and makes the submit prove it fired
    # by producing the row rather than an error.
    page.locator("#lisa-koduleht").locator("[name=url]").focus()
    page.keyboard.type(KODA_URL)
    page.locator("#lisa-koduleht").get_by_role("button", name=PLAN_BUTTON).focus()
    page.keyboard.press("Enter")
    chronology(page).get_by_role("link", name="Ava ülevaade või uudis").wait_for()


def test_the_publish_disclosure_opens_from_the_keyboard(page, base_url):
    """A native `<summary>`, operated the way a keyboard user operates one."""
    sign_in(page, base_url, SANDRA)
    plan_one(page, base_url)

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
    ), "the Ülevaated / uudised strip makes the Teema page scroll sideways at 375px"
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
    expect(chronology(page).get_by_role("link", name="Ava ülevaade või uudis")).to_be_visible()


def test_half_a_publication_is_refused_and_keeps_what_was_typed(page, base_url):
    """A date without an address is a mistake, not a plan with a note attached.

    The refusal comes back through HTMX with the panel reopened and the date
    still in the box — losing it would cost the one fact they opened the panel
    to record.

    **This is the date-only half, and after docs/adr/0085 §3 it is the only half
    a browser reaches by leaving a box alone.** Typing an address now fills the
    date beside it, so «address, no date» is something a person has to *do* —
    clear the box — rather than something they can arrive at by not typing. That
    path has its own test (`test_a_refusal_keeps_an_emptied_date_empty`), and
    the server refuses both halves identically whatever the browser did
    (`tests/test_overview_news_publication.py`).
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-koduleht")

    panel = page.locator("#lisa-koduleht")
    panel.locator("[name=published_on]").fill("14.03.2026")
    panel.get_by_role("button", name=PLAN_BUTTON).click()
    page.wait_for_timeout(200)

    reopened = page.locator("#lisa-koduleht")
    expect(reopened).to_be_visible()
    expect(reopened.locator(".field__error").first).to_be_visible()
    assert reopened.locator("[name=published_on]").input_value() == "14.03.2026"
    assert reopened.locator("[name=url]").input_value() == ""
    # Nothing was filed.
    expect(strip(page)).to_have_count(0)


def test_typing_only_an_address_now_records_a_publication(page, base_url):
    """The behaviour change docs/adr/0085 §3 makes, stated where it is visible.

    Before the default, somebody who pasted an address and pressed the button
    met a refusal asking for a date they would then type by hand — on the
    overwhelmingly common day, today. Now the date is already there, visibly, so
    the commonest publication is one paste and one click.

    A person who did *not* mean today still sees the value before saving and can
    change or clear it, which is the whole difference between a default and a
    stamp.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-koduleht")

    panel = page.locator("#lisa-koduleht")
    panel.locator("[name=url]").fill(KODA_URL)
    panel.get_by_role("button", name=PLAN_BUTTON).click()
    chronology(page).wait_for(state="visible")

    expect(chronology(page)).to_contain_text("Avaldatud")
    expect(chronology(page).get_by_role("link", name="Ava ülevaade või uudis")).to_be_visible()
    expect(strip(page)).to_have_count(0)


def test_a_credential_bearing_address_is_refused_from_the_panel_too(page, base_url):
    """The address rule is the service's and reaches the new path unchanged."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-koduleht")

    panel = page.locator("#lisa-koduleht")
    panel.locator("[name=url]").fill("https://kasutaja:parool@example.com/uudised/x")
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
    ), "the + Ülevaade / uudis panel makes the Teema page scroll sideways at 375px"


# ---------------------------------------------------------------------------
# docs/adr/0085 — one neutral activity, any public address, and a date default
# that arrives with the published path
# ---------------------------------------------------------------------------


def panel_of(page):
    return page.locator("#lisa-koduleht")


def test_the_panel_offers_one_activity_and_no_kind_selector(page, base_url):
    """docs/adr/0085 §1. An overview and a news item are the same act.

    The chip says so, and there is nothing on the panel asking which of the two
    this is — no radios, no select, no chip group. The link is what tells them
    apart, and a plan does not have one yet.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-koduleht")

    expect(page.get_by_text("+ Ülevaade / uudis", exact=True)).to_be_visible()
    panel = panel_of(page)
    expect(panel.locator("input[type=radio]")).to_have_count(0)
    expect(panel.locator("select")).to_have_count(0)
    expect(panel.locator("[data-chipgroup]")).to_have_count(0)
    # And still nothing else: no title, no description, no attachment
    # (docs/adr/0081 §2).
    expect(panel.locator("textarea")).to_have_count(0)
    expect(panel.locator("input[type=file]")).to_have_count(0)


def test_typing_a_link_changes_the_date_box_not_at_all(page, base_url):
    """docs/adr/0089 §8's island stays gone, and docs/adr/0095 §5's default is not it.

    The two are easy to confuse and the difference is the whole decision. What
    ADR 0085 §3 added and ADR 0089 §8 withdrew was a date written into the box
    **in response to a keystroke** — a plausible day appearing under somebody's
    cursor, accepted without being read. What this panel has now is a server-side
    `initial`: the day is in the box before anything is typed, where it is part
    of the form somebody is reading.

    So the claim is that the value does not *move*. Typed one character at a
    time and then in full, because the old trigger fired on the transition out
    of an empty box and a `fill()` alone would not have proved its absence.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-koduleht")

    panel = panel_of(page)
    date_box = panel.locator("[name=published_on]")
    opened_on = date_box.input_value()
    assert date_box.get_attribute("data-publication-default") is None
    assert panel.locator("[name=url]").get_attribute("data-publication-trigger") is None
    assert opened_on, "the box opens on today since docs/adr/0095 §5"

    panel.locator("[name=url]").type("h")
    expect(date_box).to_have_value(opened_on)

    panel.locator("[name=url]").fill(KODA_URL)
    expect(date_box).to_have_value(opened_on)


def test_an_address_with_no_date_is_filed_as_a_publication(page, base_url):
    """docs/adr/0089 §8, end to end and in the words a lawyer reads.

    The reported case exactly: an address pasted out of a mail, with no idea
    which day the page went up. It used to meet a refusal; it now files a
    publication that says its date is unknown rather than one dated today.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-koduleht")

    panel = panel_of(page)
    panel.locator("[name=url]").fill(KODA_URL)
    # The box opens on today since docs/adr/0095 §5, so «no date» is now
    # something a person *does* — and clearing it is still a real answer that
    # stores `NULL` and reads «Kuupäev teadmata» (docs/adr/0078 §2).
    panel.locator("[name=published_on]").fill("")
    panel.get_by_role("button", name=PLAN_BUTTON).click()
    chronology(page).wait_for(state="visible")

    expect(chronology(page)).to_contain_text("Avaldatud")
    expect(chronology(page)).to_contain_text("Kuupäev teadmata")
    expect(chronology(page).get_by_role("link", name="Ava ülevaade või uudis")).to_be_visible()
    # And no plan is left behind claiming the write-up is still owed.
    expect(strip(page)).to_have_count(0)


def test_a_date_typed_by_hand_is_what_gets_stored(page, base_url):
    """Scenario F. Nothing about the dated case changed."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-koduleht")

    panel = panel_of(page)
    panel.locator("[name=url]").fill(KODA_URL)
    panel.locator("[name=published_on]").fill("14.03.2026")
    panel.get_by_role("button", name=PLAN_BUTTON).click()
    chronology(page).wait_for(state="visible")

    expect(chronology(page)).to_contain_text("14.3.2026")
    expect(chronology(page)).not_to_contain_text("Kuupäev teadmata")
    expect(chronology(page).get_by_role("link", name="Ava ülevaade või uudis")).to_be_visible()


def test_a_recorded_date_can_be_cleared_from_the_row_and_stays_cleared(page, base_url):
    """Scenario G, in a browser: the gesture the lawyer feedback asked for.

    `Paranda link` opens on the row's own date, the box is emptied, and the row
    goes on being a publication — reading «Kuupäev teadmata» rather than today.
    Reopening the form afterwards shows the box still empty, which is the half
    that would catch an `initial` creeping back in.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-koduleht")

    panel = panel_of(page)
    panel.locator("[name=url]").fill(KODA_URL)
    panel.locator("[name=published_on]").fill("14.03.2026")
    panel.get_by_role("button", name=PLAN_BUTTON).click()
    chronology(page).get_by_text("14.3.2026").first.wait_for()

    chronology(page).get_by_role("button", name="Paranda link").first.click()
    form = chronology(page).locator("form").first
    form.wait_for(state="visible")
    # The correction form redisplays the stored value in the repository's own
    # short Estonian form, `j.n.Y`, not the zero-padded form somebody typed.
    expect(form.locator("[name=published_on]")).to_have_value("14.3.2026")
    form.locator("[name=published_on]").fill("")
    form.get_by_role("button", name="Salvesta").click()
    chronology(page).get_by_text("Kuupäev teadmata").first.wait_for()

    expect(chronology(page)).to_contain_text("Avaldatud")
    expect(chronology(page)).not_to_contain_text("14.3.2026")

    chronology(page).get_by_role("button", name="Paranda link").first.click()
    reopened = chronology(page).locator("form").first
    reopened.wait_for(state="visible")
    expect(reopened.locator("[name=published_on]")).to_have_value("")


def test_an_untouched_panel_is_refused_rather_than_filing_a_plan(page, base_url):
    """docs/adr/0083 §2's third answer, retired — and refused where somebody sees it.

    Nobody touches the link box, so the submit is a panel that looks untouched —
    and a save whose meaning is what was *not* typed is the one shape a composer
    may not have. The refusal names the missing address and stays in the panel,
    holding the day the box opened on (docs/adr/0095 §5).
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-koduleht")

    panel = panel_of(page)
    opened_on = panel.locator("[name=published_on]").input_value()
    panel.get_by_role("button", name=PLAN_BUTTON).click()

    reopened = panel_of(page)
    expect(reopened.locator(".field__error").first).to_be_visible()
    expect(reopened.locator("[name=published_on]")).to_have_value(opened_on)
    expect(strip(page)).to_have_count(0)
    expect(chronology(page)).not_to_contain_text("Avaldatud")


def test_a_news_item_on_somebody_elses_site_is_recorded(page, base_url):
    """docs/adr/0085 §2. The write-up is as often in a trade paper as on koda.ee."""
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-koduleht")

    news = "https://uudised.example/2026/03/kaubanduskoda-hoiatab"
    panel = panel_of(page)
    panel.locator("[name=url]").fill(news)
    panel.locator("[name=published_on]").fill("14.03.2026")
    panel.get_by_role("button", name=PLAN_BUTTON).click()
    chronology(page).wait_for(state="visible")

    link = chronology(page).get_by_role("link", name="Ava ülevaade või uudis")
    expect(link).to_have_attribute("href", news)
    expect(link).to_have_attribute("rel", "noopener noreferrer")
    # Labelled, never printed — which matters more now that the host is not fixed.
    expect(chronology(page)).not_to_contain_text(news)


def test_a_refusal_keeps_a_typed_date_and_an_empty_address(page, base_url):
    """A swap preserves what was typed *and* what was deliberately not typed.

    The refusal is now the *other* half of the pair — a date with nothing to
    open, which is still a claim about nothing (docs/adr/0089 §8). What it must
    not do is put anything back in the boxes.
    """
    sign_in(page, base_url, SANDRA)
    a_new_matter(page, base_url)
    open_add_panel(page, "lisa-koduleht")

    panel = panel_of(page)
    panel.locator("[name=published_on]").fill("14.03.2026")
    panel.get_by_role("button", name=PLAN_BUTTON).click()
    page.wait_for_timeout(300)

    reopened = panel_of(page)
    expect(reopened).to_be_visible()
    assert reopened.locator("[name=url]").input_value() == ""
    assert reopened.locator("[name=published_on]").input_value() == "14.03.2026"
    expect(reopened.locator(".field__error").first).to_be_visible()
    expect(strip(page)).to_have_count(0)
