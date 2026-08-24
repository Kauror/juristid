"""Ülevaade's Seis strip: every figure is a promise that a list exists.

The complaint this suite exists for has not changed with the rebuild — a number
somebody cannot follow is a number they stop trusting, and a number whose list
holds a different count is worse than no number at all. What changed is where
the numbers live: the KPI card row became a one-line strip, and its figures lead
to the register, to the canonical opinion list, and to this page's own
intervention list.

That last destination is deliberate and is the one thing worth explaining.
*Üle tähtaja* counts late **work** — a DO deadline and an Oluline tähtaeg
alike — and the register can only filter Matters by their open action, so a
link there would open a list shorter than the number above it. `?sekkumine=`
narrows the list this page already renders, which is the only destination that
can hold exactly what the figure counted.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import HEAD, sign_in

pytestmark = pytest.mark.e2e

#: The figures that open the register, by the caption printed beside them.
#: Matched loosely so rewording the copy does not break a navigation test —
#: what is asserted is the behaviour, not the sentence.
REGISTER_FIGURES = {
    "open": "avatud teemat",
    "no_action": "järgmise tegevuseta",
    "deadlines": "tähtaega",
}


def figure(page, caption: str):
    return page.locator(".seis__figure").filter(has_text=caption).first


def figure_count(page, caption: str) -> int:
    return int(figure(page, caption).locator(".seis__number").inner_text().strip())


def shown_total(page) -> int:
    """What the register says it is showing, from its own live count element."""
    text = page.locator(".registercount").inner_text()
    match = re.search(r"\d+", text)
    assert match, f"the register printed no count: {text!r}"
    return int(match.group(0))


def open_figure(page, base_url, caption: str) -> int:
    """Read a figure, click it, and return the number it claimed."""
    page.goto(f"{base_url}/ulevaade/")
    page.wait_for_load_state("networkidle")
    expect(figure(page, caption)).to_be_visible()
    claimed = figure_count(page, caption)
    figure(page, caption).click()
    page.wait_for_load_state("networkidle")
    return claimed


@pytest.mark.parametrize("key", sorted(REGISTER_FIGURES))
def test_a_figure_opens_the_register_already_filtered(page, base_url, key):
    """The whole complaint, in one assertion per figure.

    The register must arrive holding the figure's own number *and* say which
    filter produced it. A count that happens to match with no chip on screen is
    the failure this replaced — an unfiltered register that coincidentally has
    the same number of rows.
    """
    sign_in(page, base_url, HEAD)
    claimed = open_figure(page, base_url, REGISTER_FIGURES[key])

    assert "/teemad/?" in page.url, f"{key} did not open the register"
    assert shown_total(page) == claimed, f"{key}: the figure and the list disagree"
    expect(page.locator(".filterchip")).not_to_have_count(0)


@pytest.mark.parametrize("key", sorted(REGISTER_FIGURES))
def test_the_filter_a_figure_applied_can_be_cleared(page, base_url, key):
    """Arriving from a number must not be a dead end.

    "Tühjenda kõik" returns to the ordinary register, which is how somebody who
    clicked the wrong figure gets back to their work.
    """
    sign_in(page, base_url, HEAD)
    open_figure(page, base_url, REGISTER_FIGURES[key])
    filtered = shown_total(page)

    page.locator(".filterchip--clear").click()
    page.wait_for_load_state("networkidle")

    assert shown_total(page) >= filtered
    expect(page.locator(".filterchip--clear")).to_have_count(0)


def test_the_back_button_returns_to_the_overview(page, base_url):
    """Nothing about this navigation depends on session state.

    The filter lives in the URL, so Back is Back — a filter held in a cookie is
    a page that cannot be shared and cannot be reproduced from a bug report.
    """
    sign_in(page, base_url, HEAD)
    open_figure(page, base_url, REGISTER_FIGURES["no_action"])
    page.go_back()
    page.wait_for_load_state("networkidle")

    expect(page.locator(".seis__figure").first).to_be_visible()


def test_the_overdue_figure_opens_a_list_that_holds_exactly_what_it_counted(page, base_url):
    """The figure counts late work; the list it opens holds late work.

    Both a genuinely late DO and a WAIT past its review date exist in the seeded
    world, so a filter that collected reviews as well as deadlines would fail
    here rather than merely be too generous (master specification 18.8).
    """
    from app.core.management.commands.seed_e2e_data import OVERDUE_TITLE, REVIEW_DUE_TITLE

    sign_in(page, base_url, HEAD)
    claimed = open_figure(page, base_url, "üle tähtaja")

    assert "sekkumine=hilinenud" in page.url
    rows = page.locator(".interrow")
    assert rows.count() == claimed, "the figure and its list disagree"
    expect(rows.filter(has_text=OVERDUE_TITLE)).to_have_count(1)
    expect(rows.filter(has_text=REVIEW_DUE_TITLE)).to_have_count(0)


def test_the_stalled_figure_opens_the_matters_with_no_next_action(page, base_url):
    from app.core.management.commands.seed_e2e_data import UNASSIGNED_TITLE

    sign_in(page, base_url, HEAD)
    open_figure(page, base_url, REGISTER_FIGURES["no_action"])

    rows = page.locator(".table--register tbody tr")
    # The unassigned Matter has no next action either, so it is in this list —
    # what matters is that the list is filtered at all, which the chip proves.
    expect(rows.filter(has_text=UNASSIGNED_TITLE)).to_have_count(1)


def test_the_opinion_figure_opens_the_canonical_sent_list(page, base_url):
    """Sent Submissions, narrowed to this month — never the historical archive."""
    sign_in(page, base_url, HEAD)
    open_figure(page, base_url, "esitatud arvamust")

    assert "/arvamused/" in page.url
    assert "kuu=" in page.url


def test_no_figure_leads_nowhere(page, base_url):
    sign_in(page, base_url, HEAD)
    page.goto(f"{base_url}/ulevaade/")
    page.wait_for_load_state("networkidle")

    figures = page.locator(".seis__figure")
    assert figures.count() >= 4
    for index in range(figures.count()):
        href = figures.nth(index).get_attribute("href")
        assert href and href != "#", "a Seis figure leads nowhere"
