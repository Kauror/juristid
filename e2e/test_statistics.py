"""Statistika, in a browser.

Everything about what a number *means* is proved against the database in
`tests/test_reporting_*`. What only a browser shows is whether the workspace
works for a person: whether the filter survives a Back button, whether clicking
a bar reaches the records behind it, whether a chart is readable without seeing
it, and whether changing persona changes what the page is allowed to say.

Deliberately few, like the rest of the browser suite. These exist for the
failures that live between layers, and visual assertions cost more than they
catch (docs/adr/0010).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import (
    OPEN_TITLE,
    OVERDUE_TITLE,
    RESTRICTED_TITLE,
    SUBMISSION_TITLE,
)
from e2e.conftest import ADMIN, MARTIN, READER, SANDRA, go_to, sign_in, sign_out

pytestmark = pytest.mark.e2e


def open_statistics(page, base_url: str, tab: str = "", period: str = "koik") -> None:
    page.goto(f"{base_url}/statistika/{tab}?periood={period}")
    page.wait_for_load_state("networkidle")


def headline(page) -> int:
    """The first metric card's number, as an integer."""
    return int(page.locator(".metric__value").first.inner_text().strip())


def test_statistika_is_a_destination_of_its_own(page, base_url, screenshots):
    """Not another tab on Ülevaade.

    Ülevaade answers "what needs attention now" and is a work surface; this
    answers "what does the corpus say" and is a reading surface. Overloading one
    page with both makes neither readable (Stage-2E brief 6).
    """
    sign_in(page, base_url, MARTIN)
    go_to(page, "Statistika")
    page.wait_for_url(f"{base_url}/statistika/")

    expect(page.get_by_role("heading", name="Statistika")).to_be_visible()
    # Scoped to the tab strip. `Teemad` is also a main-navigation item, and an
    # unscoped lookup matches both — which is Playwright telling us the two
    # surfaces really are distinct, exactly as intended.
    tabs = page.get_by_label("Statistika vaated")
    for tab in ("Üldpilt", "Teemad", "Koja tegevus", "Ajalooline materjal", "Andmekvaliteet"):
        expect(tabs.get_by_role("link", name=tab, exact=True)).to_be_visible()

    screenshots(page, "statistika-ulevaade")


@pytest.mark.parametrize(
    ("tab", "heading_text"),
    [
        ("teemad/", "Jaotused"),
        ("tegevus/", "Näitajad"),
        ("ajalooline/", "Maht"),
        ("andmekvaliteet/", "Järjekorrad"),
    ],
)
def test_every_tab_renders(page, base_url, screenshots, tab, heading_text):
    sign_in(page, base_url, MARTIN)
    open_statistics(page, base_url, tab)
    expect(page.get_by_role("heading", name=heading_text, exact=True).first).to_be_visible()
    screenshots(page, f"statistika-{tab.strip('/')}")


def test_the_period_filter_changes_the_page_and_survives_the_back_button(page, base_url):
    """Filter state lives in the URL, so Back is a real undo.

    A saved-view framework is what a product invents when it cannot do this.
    """
    sign_in(page, base_url, MARTIN)
    open_statistics(page, base_url, "teemad/")
    everything = headline(page)

    page.get_by_role("link", name="Käesolev aasta").click()
    page.wait_for_load_state("networkidle")
    assert "periood=kaesolev" in page.url

    page.go_back()
    page.wait_for_load_state("networkidle")
    assert "periood=koik" in page.url
    assert headline(page) == everything


def test_a_chart_segment_opens_the_records_behind_it(page, base_url, screenshots):
    """The promise every number on this workspace makes."""
    sign_in(page, base_url, MARTIN)
    open_statistics(page, base_url, "teemad/")

    bar = page.locator("a.barchart__row").first
    expected = bar.locator(".barchart__value").inner_text().strip()
    bar.click()
    page.wait_for_load_state("networkidle")

    expect(page.get_by_role("heading", name="Teemad")).to_be_visible()
    expect(page.locator(".pagehead__context")).to_contain_text(f"{expected} teemat")
    screenshots(page, "statistika-drillthrough")


def test_an_overdue_count_reaches_the_matter_that_is_late(page, base_url):
    """Only a DO with a deadline is late, and the link proves which one."""
    sign_in(page, base_url, MARTIN)
    open_statistics(page, base_url, "tegevus/")

    card = page.locator(".metric").filter(has_text="Tähtaeg möödas").first
    card.locator("a.metric__value").click()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("link", name=OVERDUE_TITLE)).to_be_visible()


def test_a_signed_container_is_never_shown_as_a_failure(page, base_url, screenshots):
    """ASiC-E is valid historical material that nothing will ever open.

    `Ei kohaldu` is the successful state for it, and the page has to keep that
    apart from a parse failure — otherwise an operator goes looking for a defect
    that is a deliberate decision (Stage-2E brief 31).
    """
    sign_in(page, base_url, MARTIN)
    open_statistics(page, base_url, "andmekvaliteet/")

    states = page.locator("section[aria-labelledby='eraldamine-jaotus-heading']")
    expect(states).to_contain_text("Ei kohaldu")
    failures = page.locator(".queuerow").filter(has_text="Teksti eraldamine ebaõnnestus")
    expect(failures).to_have_count(0)

    open_statistics(page, base_url, "ajalooline/")
    signed = page.locator(".metric").filter(has_text="Digiallkirjastatud materjale").first
    expect(signed).to_be_visible()
    # The card carries a count *and* says what the count is not. Asserting the
    # sentence rather than the absence of a word: the note names extraction
    # failure precisely in order to rule it out, so forbidding the word would
    # forbid the explanation.
    expect(signed).to_contain_text("ei kuulu kunagi eraldamise ebaõnnestumiste hulka")
    assert int(signed.locator(".metric__value").inner_text().strip()) >= 1
    screenshots(page, "statistika-allkirjastatud")


def test_the_reconciliation_count_links_to_the_review_queue(page, base_url):
    """An administrator's surface, reached from the number that describes it."""
    sign_in(page, base_url, ADMIN)
    open_statistics(page, base_url, "andmekvaliteet/")

    # The conflict candidate, because it is the one no other browser test
    # decides. This suite shares a single seeded database.
    row = page.locator(".queuerow").filter(has_text="Ajaloo sidumise vastuolud").first
    row.get_by_role("link").first.click()
    page.wait_for_load_state("networkidle")
    assert "/haldus/ajaloo-ulevaatus/" in page.url


def test_the_headline_agrees_with_the_register_for_each_persona(page, base_url):
    """Two views, one population, per reader.

    The Statistika card and the Teemad register compute their totals through
    entirely different code. If either forgot to scope, they would disagree —
    and Sandra, who owns the restricted file, must see strictly more than Martin,
    who does not.

    Deliberately *not* an assertion on absolute numbers: this suite shares one
    seeded database with tests that create Matters. The exact arithmetic of
    "exactly one more" is proved against a controlled world in
    `tests/test_reporting_authorization.py`; what a browser adds is that the two
    surfaces agree and that the title never leaks.
    """
    totals = {}
    for persona in (READER, SANDRA):
        sign_in(page, base_url, persona)
        open_statistics(page, base_url, "teemad/")
        totals[persona.upn] = headline(page)
        if persona is READER:
            assert RESTRICTED_TITLE not in page.content()

        page.goto(f"{base_url}/teemad/?olek=koik")
        page.wait_for_load_state("networkidle")
        register = page.locator(".pagehead__context").inner_text()
        assert register.startswith(f"{totals[persona.upn]} "), (
            f"{persona.upn}: Statistika says {totals[persona.upn]}, register says {register}"
        )
        sign_out(page, base_url)

    assert totals[SANDRA.upn] > totals[MARTIN.upn]


def test_a_csv_export_respects_the_filter_that_was_on_screen(page, base_url):
    # A reader, because the assertion below is that the restricted row is not in
    # the export — and since docs/adr/0042 a lawyer's export legitimately has it.
    sign_in(page, base_url, READER)
    open_statistics(page, base_url, "teemad/", period="kaesolev")

    with page.expect_download() as download:
        page.get_by_role("link", name="Laadi CSV").click()
    saved = download.value
    assert saved.suggested_filename == "teemad.csv"

    body = saved.path().read_bytes().decode("utf-8-sig")
    assert OPEN_TITLE in body
    assert RESTRICTED_TITLE not in body


def test_a_definition_is_available_beside_every_number(page, base_url, screenshots):
    """ "Kuidas arvutatakse?" opens the reviewed definition in place."""
    sign_in(page, base_url, MARTIN)
    open_statistics(page, base_url)

    disclosure = page.locator("details.definition").first
    disclosure.get_by_text("Kuidas arvutatakse?").click()
    expect(disclosure).to_contain_text("Populatsioon")
    expect(disclosure).to_contain_text("Ajavaade")
    screenshots(page, "statistika-definitsioon")


def test_a_bar_is_as_long_as_its_number(page, base_url):
    """The defect a screenshot found and no count-based test could.

    Django localizes numbers rendered through a template, so `width:25,0%`
    reached the browser, which dropped the declaration — and every bar filled
    its track while the number printed beside it stayed correct. Four bars
    reading 1, 0, 0 and 0 were all the same length.

    So this measures pixels: the largest bar is wider than a zero one.
    """
    sign_in(page, base_url, MARTIN)
    open_statistics(page, base_url, "ajalooline/")

    chart = page.locator("section.chart").filter(has_text="Materjalide ülekandmise seis").first
    rows = chart.locator(".barchart__row")
    widths = []
    for index in range(rows.count()):
        row = rows.nth(index)
        value = int(row.locator(".barchart__value").inner_text().strip())
        box = row.locator(".barchart__fill").bounding_box()
        widths.append((value, box["width"] if box else 0.0))

    assert widths, "the materialisation chart drew no bars"
    largest = max(widths, key=lambda pair: pair[0])
    for value, width in widths:
        if value == 0:
            assert width < largest[1], f"a bar of 0 is as wide as one of {largest[0]}"


def test_a_chart_can_be_read_as_a_table(page, base_url):
    """Nobody has to estimate a value from the length of a bar."""
    sign_in(page, base_url, MARTIN)
    open_statistics(page, base_url, "teemad/")

    # A *composition* chart: the trend's fallback table is period and count,
    # with no share column, and it is the first one on the page.
    chart = page.locator("section.chart").filter(has_text="Teemad kirje liigi järgi").first
    chart.get_by_text("Vaata tabelina").click()

    table = chart.locator("table.data-table")
    expect(table).to_be_visible()
    expect(table.locator("th", has_text="Osakaal")).to_be_visible()
    expect(table.locator("th", has_text="Täielik")).to_be_visible()


def test_the_charts_carry_a_name_and_a_description_for_a_screen_reader(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_statistics(page, base_url)

    trend = page.locator("svg.trend").first
    expect(trend).to_have_attribute("role", "img")
    assert trend.get_attribute("aria-labelledby")

    for section in page.locator("section.chart").all()[:3]:
        assert section.get_attribute("aria-labelledby")


def test_the_workspace_is_operable_from_the_keyboard(page, base_url):
    """Focus the first bar and open it with Enter."""
    sign_in(page, base_url, MARTIN)
    open_statistics(page, base_url, "teemad/")

    first_bar = page.locator("a.barchart__row").first
    first_bar.focus()
    expect(first_bar).to_be_focused()
    first_bar.press("Enter")
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("heading", name="Teemad")).to_be_visible()


def test_a_submission_number_reaches_the_submission_list(page, base_url):
    """The product's only list of sent opinions, and the number above it."""
    sign_in(page, base_url, MARTIN)
    open_statistics(page, base_url, "tegevus/")

    card = page.locator(".metric").filter(has_text="Saadetud arvamusi").first
    card.locator("a.metric__value").click()
    page.wait_for_load_state("networkidle")

    expect(page.get_by_role("heading", name="Saadetud arvamused")).to_be_visible()
    expect(page.get_by_text(SUBMISSION_TITLE).first).to_be_visible()


# ---------------------------------------------------------------------------
# Statistics 2.0
#
# Four things only a browser proves about this pass: that the landing page's
# five groups are actually there and in order, that a wide matrix scrolls
# instead of pushing the page sideways, that the comparison card reads as text
# rather than as a colour, and that a register-only lawyer's name survives all
# the way to the screen.
# ---------------------------------------------------------------------------


def test_the_landing_page_answers_its_five_questions_in_order(page, base_url, screenshots):
    """Põhinäitajad, muutus, pikk ajajoon, praegune portfell, andmekatvus.

    Order matters: somebody opening this page is asking "how much is there",
    then "is that more or less than before", then "what is on the desk now".
    """
    sign_in(page, base_url, MARTIN)
    open_statistics(page, base_url)

    # Compared case-insensitively: `.sectionlabel` is uppercased in CSS, and
    # `inner_text()` returns rendered text. Asserting the rendered casing would
    # make this test fail the day somebody changes a `text-transform`, which is
    # not what it is here to catch.
    headings = page.locator("h2.sectionlabel")
    rendered = [headings.nth(i).inner_text().strip().lower() for i in range(headings.count())]
    assert rendered == [
        "põhinäitajad",
        "muutus eelmise aastaga",
        "pikk ajajoon",
        "praegune portfell",
        "andmekatvus",
    ]
    screenshots(page, "statistika-ulevaade-2")


def test_the_two_long_trends_start_in_different_years(page, base_url):
    """The register begins in 2011 and the archive in 2020.

    A reader must be able to see that the archive chart simply has no earlier
    bars, rather than a run of zeros implying Koda sent nothing before 2020.
    """
    sign_in(page, base_url, MARTIN)
    open_statistics(page, base_url)

    archive = page.locator("section.chart").filter(has_text="Arvamuste arhiiv aastate kaupa")
    expect(archive).to_be_visible()
    archive.get_by_text("Vaata tabelina").click()
    years = archive.locator("table.data-table tbody th").all_inner_texts()
    assert years, "the archive trend drew no years"
    assert all(int(year.strip()) >= 2020 for year in years)
    assert "2011" not in years


def test_the_comparison_card_is_readable_without_seeing_a_colour(page, base_url, screenshots):
    """No arrow, no red, no green. The change is a sentence (brief 34, 81)."""
    sign_in(page, base_url, MARTIN)
    open_statistics(page, base_url)

    card = page.locator(".comparison").first
    expect(card).to_be_visible()
    text = card.inner_text()
    assert "rohkem" in text or "vähem" in text or "muutumatu" in text
    # Both windows are printed, so nobody has to assume the two sides are
    # comparable. That assumption is what makes a part year look like a collapse.
    expect(card.get_by_text("Praegune periood")).to_be_visible()
    expect(card.get_by_text("Võrreldav varasem periood")).to_be_visible()
    screenshots(page, "statistika-vordlus")


def test_a_previous_period_of_zero_shows_no_percentage(page, base_url):
    """The seeded archive's previous comparable window is empty.

    So the card must say the percentage is unavailable rather than print an
    infinity or a fabricated 100 % (brief 73).
    """
    sign_in(page, base_url, MARTIN)
    open_statistics(page, base_url)

    card = page.locator(".comparison").filter(has_text="Arhiivi arvamuste muutus").first
    expect(card).to_be_visible()
    assert "∞" not in card.inner_text()


def test_the_responsibility_matrix_is_a_real_table_that_scrolls(page, base_url, screenshots):
    """Semantic table, headed rows and columns, inside its own scroll container.

    The failure a browser catches: a wide grid that widens the page instead of
    scrolling, which makes every other section unreadable on a laptop.
    """
    sign_in(page, base_url, MARTIN)
    open_statistics(page, base_url, "teemad/")

    section = page.locator("section.chart").filter(has_text="Teemad aastate ja vastutuse kaupa")
    expect(section).to_be_visible()
    table = section.locator("table.matrix")
    expect(table).to_be_visible()
    expect(table.locator("thead th[scope='col']").first).to_be_visible()
    expect(table.locator("tbody th[scope='row']").first).to_be_visible()
    expect(table.locator("tfoot")).to_be_visible()

    body_width = page.evaluate("document.body.scrollWidth")
    viewport = page.evaluate("window.innerWidth")
    assert body_width <= viewport + 1, f"the page scrolls sideways: {body_width} > {viewport}"
    screenshots(page, "statistika-maatriks")


# A browser test for the register-only lawyer would need a seeded Matter whose
# VASTUTAJA names somebody with no account. Adding one moved the register, the
# dashboard and the search baselines — pages this branch does not own — because
# the browser suite shares a single seeded world with the visual suite. The rule
# is proved against a controlled world in
# `tests/test_reporting_responsibility.py` instead, and perturbing another
# agent's baselines to restate it here would cost more than it is worth.


def test_the_archive_block_is_kept_apart_from_the_canonical_one(page, base_url, screenshots):
    """Two headings, two date bases, and nothing that invites adding them up."""
    sign_in(page, base_url, MARTIN)
    open_statistics(page, base_url, "tegevus/")

    # Scoped to `h2.sectionlabel`: a chart's own `h3` title can carry similar
    # words, and what this test is about is the two *sections* being separate.
    sections = page.locator("h2.sectionlabel")
    rendered = [
        sections.nth(index).inner_text().strip().lower() for index in range(sections.count())
    ]
    assert "saadetud arvamused" in rendered
    assert "arvamuste arhiiv ajas" in rendered

    archive_section = page.locator("section").filter(has=page.locator("h2#tegevus-arhiiv-heading"))
    assert "failinimest" in archive_section.first.inner_text()
    screenshots(page, "statistika-arhiiv-ajas")


def test_no_statistics_page_calls_a_count_a_workload(page, base_url):
    """The wording rule, checked where a reader actually meets it.

    Deliberately not a search for *edetabel*: a definition panel is allowed to
    say a table is "not a league table", and whether a collapsed `<details>`
    counts as rendered text is a browser detail rather than a product rule. The
    catalogue test covers the hedged wording properly; these four have no
    innocent reading.
    """
    sign_in(page, base_url, MARTIN)
    for tab in ("", "teemad/", "tegevus/"):
        open_statistics(page, base_url, tab)
        body = page.locator("body").inner_text().lower()
        for forbidden in ("produktiivsus", "parim jurist", "kõige tootlikum", "tulemusreiting"):
            assert forbidden not in body, f"{tab}: {forbidden}"


@pytest.mark.parametrize("width", [1440, 1280, 1024])
def test_the_workspace_survives_a_narrower_laptop(page, base_url, screenshots, width):
    """Charts reflow; the matrix scrolls inside itself; nothing overflows.

    The widths are the ones the shell's own visual baselines cover. Below 1024
    the application's chrome overflows on every page, which is a product-wide
    decision rather than something this branch introduced or should assert on.
    """
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    open_statistics(page, base_url, "teemad/")

    body_width = page.evaluate("document.body.scrollWidth")
    assert body_width <= width + 1, f"{width}px: page scrolls sideways ({body_width})"
    screenshots(page, f"statistika-teemad-{width}")
