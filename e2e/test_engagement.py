"""`Kaasamine` in a real browser: record a consultation from the composer.

The approved Teema target removed the standalone `Kaasamine` section and put
recording one behind the composer chip `+ Kaasamine`, beside the four other
progressive panels, saved by the composer's one `Salvesta`. This file follows it
there (docs/adr/0074 §9).

What only a browser can check is the same as before, moved: that the disclosure
actually opens, that its chip group is single-select against the hidden field the
server validates, that the one save writes the note *and* the engagement, and
that the HTMX swap puts the result on the page without a reload.

**These write to the restricted Matter, not the ordinary one.** The screenshot
suite opens `OPEN_TITLE`, and a chronology that grew while these ran would make
that baseline depend on test order. Everything written here happens somewhere the
camera never points.
"""

from __future__ import annotations

from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import ARCHIVE_TITLE, RESTRICTED_TITLE
from e2e.conftest import SANDRA, open_add_panel, open_composer, sign_in


def panel(page):
    """The `+ Kaasamine` panel under `LISA TEEMALE`, closed until asked for."""
    return page.locator("#lisa-kaasamine")


def open_panel(page):
    """Open it, and return it. Idempotent, so a test can call it twice."""
    open_add_panel(page, "lisa-kaasamine")
    return panel(page)


def open_scratch_matter(page, base_url: str) -> None:
    """Sandra's restricted Matter — writable by her, and never screenshotted."""
    page.goto(f"{base_url}/teemad/?olek=koik&q=Konfidentsiaalne")
    page.wait_for_load_state("networkidle")
    page.get_by_role("link", name=RESTRICTED_TITLE, exact=False).first.click()
    page.wait_for_load_state("networkidle")


def open_empty_matter(page, base_url: str) -> None:
    """The archive record, which holds no `Kaasamine` and never will.

    The zero state cannot be read off the scratch Matter: the tests below write
    to it, so it is empty exactly once per seeded world and only until the first
    of them runs. That made the primary regression test pass in a full run and
    fail on its own — the shape of test nobody can reproduce while fixing it.
    """
    page.goto(f"{base_url}/teemad/?olek=koik&q=Arhiiviteema")
    page.wait_for_load_state("networkidle")
    page.get_by_role("link", name=ARCHIVE_TITLE, exact=False).first.click()
    page.wait_for_load_state("networkidle")


def chronology(page):
    return page.locator("#ajalugu-loend")


def test_the_matter_page_carries_no_standalone_kaasamine_section(page, base_url):
    """The section is gone from the page, not hidden on it.

    A Matter with no consultation used to spend a heading and a line saying so
    on every load; one that had them showed a standing list of dated facts the
    chronology now carries (TEEMA_TARGET_SPEC §F).
    """
    sign_in(page, base_url, SANDRA)
    open_empty_matter(page, base_url)

    expect(page.locator("#kaasamine")).to_have_count(0)
    expect(page.locator(".factspanel")).to_have_count(0)
    expect(page.get_by_text("+ Lisa kaasamine")).to_have_count(0)


def test_the_panel_opens_from_the_launcher_and_asks_three_things(page, base_url):
    """`Liik`, `Keda kaasati`, `Vastuseid` — and not the old five-field form."""
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)

    expect(panel(page)).not_to_have_attribute("open", "")
    open_panel(page)

    for label in ("Küsitlus", "Koosolek", "Kirjade voor"):
        expect(panel(page).locator(".uxchip", has_text=label)).to_have_count(1)
    expect(panel(page).locator("[name=audience]")).to_be_visible()
    expect(panel(page).locator("[name=response_count]")).to_be_visible()
    # The questions the target does not ask.
    expect(panel(page).locator("[name=url]")).to_have_count(0)
    expect(panel(page).locator("[name=note]")).to_have_count(0)
    expect(panel(page).locator("[name=occurred_on]")).to_have_count(0)
    # And its own save, which commits this operation and nothing else
    # (docs/adr/0075 §2).
    expect(panel(page).locator("button[type=submit]")).to_have_count(1)


def test_the_kind_chips_are_single_select_over_the_field_that_is_submitted(page, base_url):
    """The chip stores nothing of its own: it writes into the hidden field, which
    is what the server validates — the same contract the quick dates have."""
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)
    open_panel(page)

    field = panel(page).locator("input[name=kind]")
    # `Küsitlus` is selected on open, which is what the target shows.
    expect(field).to_have_value("SURVEY")
    expect(panel(page).locator(".uxchip.is-selected")).to_have_count(1)

    panel(page).locator(".uxchip", has_text="Koosolek").click()

    expect(field).to_have_value("MEETING")
    expect(panel(page).locator(".uxchip.is-selected")).to_have_count(1)
    expect(panel(page).locator(".uxchip.is-selected")).to_have_text("Koosolek")


def test_two_saves_write_the_note_and_the_engagement_separately(page, base_url):
    """Two intentions, two saves, and the chronology shows both.

    This reverses what the composer's single save proved. A note and a
    consultation are different things somebody chose to record, and the surface
    now asks which before it asks anything else (docs/adr/0075 §2).
    """
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)

    open_composer(page)
    page.locator("#lisa-marge .composer__body").fill("Küsisin liikmetelt tagasisidet.")
    page.locator("#lisa-marge button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    open_panel(page)
    panel(page).locator(".uxchip", has_text="Kirjade voor").click()
    panel(page).locator("[name=audience]").fill("liikmed")
    panel(page).locator("[name=response_count]").fill("9")
    panel(page).locator("button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    # The engagement, as a milestone row carrying its kind and its count.
    milestone = chronology(page).locator(".uxtl__mswhat", has_text="Kaasamine: liikmed")
    expect(milestone).to_have_count(1)
    expect(chronology(page)).to_contain_text("Vastuseid 9")
    expect(chronology(page)).to_contain_text("E-kiri või kampaania")
    # The note, as a work row of its own.
    expect(chronology(page).locator(".richtext").first).to_contain_text(
        "Küsisin liikmetelt tagasisidet"
    )
    # One act, one line: the audit event does not also print a clause.
    expect(chronology(page)).not_to_contain_text("lisas kaasamise")

    # And it reached the process strip, which is the other half of §F.
    expect(page.locator(".tl-step__what", has_text="E-kiri või kampaania")).to_have_count(1)


def test_an_engagement_with_no_audience_is_refused_with_the_panel_open(page, base_url):
    """A refusal inside a panel nobody can see is a refusal nobody reads."""
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)
    open_panel(page)

    panel(page).locator("[name=response_count]").fill("3")
    panel(page).locator("button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    expect(panel(page)).to_have_attribute("open", "")
    expect(panel(page)).to_contain_text("Kirjuta, keda kaasati")
    # With the count still in it, and no other panel opened on its behalf.
    expect(panel(page).locator("[name=response_count]")).to_have_value("3")
    expect(page.locator("#lisa-marge")).not_to_have_attribute("open", "")


def test_an_uncounted_engagement_says_nothing_about_responses(page, base_url):
    """NULL is «nobody counted», which is not «nobody answered» — so the row
    states the kind and stops (docs/adr/0074 §5)."""
    sign_in(page, base_url, SANDRA)
    open_scratch_matter(page, base_url)
    open_panel(page)

    panel(page).locator("[name=audience]").fill("kaubandusvaldkonna töögrupp")
    panel(page).locator("button[type=submit]").click()
    page.wait_for_load_state("networkidle")

    row = chronology(page).locator(
        ".uxtl__item", has=page.locator("text=Kaasamine: kaubandusvaldkonna töögrupp")
    )
    expect(row.first).to_be_visible()
    expect(row.first).not_to_contain_text("Vastuseid")
