"""Ülevaade's drill-downs, in a browser: every number opens exactly its own list.

The complaint has not changed since the KPI cards became a Seis strip — a number
somebody cannot follow is a number they stop trusting, and a number whose list
holds a different count is worse than no number at all. What changed in this
round is how much of the page is held to it.

Before, one figure was asserted at a time and *üle tähtaja* was exempted: it
counts late **work**, an ``Oluline tähtaeg`` past its day carries no open
action, and the register's ``?tegevus=`` could not express that — so the figure
narrowed this page's own intervention list instead of opening the register like
every figure beside it. ``?too=`` expresses the read model's populations
directly (``app/matters/work_items.py``), so the exemption is gone and the
figure leads where a reader expects.

What this file asserts, in a real browser, for **every** clickable number on
both scopes:

* the number is read off the page as rendered;
* the link is clicked, not synthesised;
* the destination's own count element equals it;
* the destination shows *which* filter produced the list;
* and the results section is in the viewport and holds focus, so somebody who
  clicked a number is looking at the rows rather than at a filter panel.

The Python suite beside it (``tests/test_overview_drilldowns.py``) proves the
same equality against the querysets. This proves it against the pixels, which is
the only place the fragment, the focus and the chips exist at all.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import HEAD, sign_in

pytestmark = pytest.mark.e2e

SCOPES = ("osakond", "valdkonniti")

#: Everything on the page that prints a number and links somewhere. The value
#: selector is relative to the link, or the link itself when it *is* the number.
#:
#: Kept as data so a drill-down added later is either listed here or visibly
#: absent from a suite that names its coverage.
NUMBERED_LINKS: tuple[tuple[str, str], ...] = (
    (".seis__figure", ".seis__number"),
    (".ovsection__head .ovsection__link", ""),
    # `.uxdl__all` is deliberately absent. The deadline panel's per-window
    # control used to be a link into the register and is now the summary of the
    # window's own `<details>`: it opens rows on this page, so there is no
    # destination whose count could be compared with it. What it discloses is
    # asserted in `e2e/test_department_page.py` and against the database in
    # `tests/test_department_page.py`. The section's own «Kõik tähtajad →» is
    # still navigation and is still covered, by `.ovsection__link` above.
    ("a.loadrow__open", ""),
    ("a.loadrow__overdue", ""),
    (".loadrow--unassigned", ".loadrow__open"),
    ("a.arearail", ".arearail__count"),
    ("a.railrow", ".railrow__value"),
    (".areatable__num a", ""),
    (".arearow__more", ""),
)


def first_number(text: str) -> int | None:
    """The first integer in a label, which is the number the reader sees.

    ``Ava kõik 12 teemat registris →`` and a bare ``12`` are both links whose
    promise is twelve. A label with no number at all — *Näita kõiki põhjuseid* —
    promises nothing countable and is skipped rather than guessed at.
    """
    match = re.search(r"\d+", text or "")
    return int(match.group(0)) if match else None


def drilldowns(page) -> list[dict]:
    """Every (number, destination) pair on screen, read as rendered."""
    found: list[dict] = []
    for link_selector, value_selector in NUMBERED_LINKS:
        links = page.locator(link_selector)
        for index in range(links.count()):
            link = links.nth(index)
            href = link.get_attribute("href")
            # Rows inside a collapsed <details> — the area table's per-row
            # "Näita kõiki N teemat" — are not on screen and cannot be clicked.
            # They are the same link the row's own summary already carries, so
            # skipping them loses no coverage; reading them would silently
            # produce an empty label and a claim of nothing.
            if not href or not link.is_visible():
                continue
            source = link.locator(value_selector) if value_selector else link
            if value_selector and not source.count():
                continue
            claimed = first_number(source.inner_text())
            if claimed is None:
                continue
            found.append(
                {
                    "selector": link_selector,
                    "index": index,
                    "href": href,
                    "count": claimed,
                    "label": link.inner_text().strip().replace("\n", " ")[:60],
                }
            )
    return found


def shown_total(page) -> int:
    """What the destination says it is showing, from its own count element.

    Both list surfaces print one: the register's live ``.registercount`` and the
    Arvamused workspace's ``.resultcount``. A destination with no count element
    is a destination whose promise cannot be checked, and there are none.
    """
    element = page.locator(".registercount, .resultcount").first
    expect(element).to_be_visible()
    text = element.inner_text()
    match = re.search(r"\d+", text)
    assert match, f"the destination printed no count: {text!r}"
    return int(match.group(0))


def in_viewport(page, selector: str) -> bool:
    return page.evaluate(
        """(selector) => {
            const node = document.querySelector(selector);
            if (!node) return false;
            const box = node.getBoundingClientRect();
            return box.top < window.innerHeight && box.bottom > 0;
        }""",
        selector,
    )


def open_overview(page, base_url: str, scope: str = "osakond"):
    page.goto(f"{base_url}/osakond/?vaade={scope}")
    page.wait_for_load_state("networkidle")
    return page


# ---------------------------------------------------------------------------
# The sweep: every number, every scope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scope", SCOPES)
def test_every_number_opens_a_list_holding_exactly_that_many(page, base_url, scope):
    """The whole complaint, asserted once per clickable number on the page.

    Each one is read, clicked and checked against the destination's own count.
    Compared as the two numbers a reader compares — the one they clicked and the
    one they land on — because that is the disagreement they would notice.
    """
    sign_in(page, base_url, HEAD)
    open_overview(page, base_url, scope)
    targets = drilldowns(page)
    assert targets, f"{scope} rendered no numbered links at all"

    checked = 0
    for target in targets:
        if "/teemad/" not in target["href"] and "/arvamused/" not in target["href"]:
            continue
        open_overview(page, base_url, scope)
        link = page.locator(target["selector"]).nth(target["index"])
        expect(link).to_be_visible()
        link.click()
        page.wait_for_load_state("networkidle")
        assert shown_total(page) == target["count"], (
            f"{scope}: {target['label']!r} claims {target['count']}, {page.url} disagrees"
        )
        checked += 1

    assert checked, f"{scope} has no list destinations to check"


@pytest.mark.parametrize("scope", SCOPES)
def test_arriving_from_a_number_shows_which_filter_produced_the_list(page, base_url, scope):
    """A filtered list with no visible filter is indistinguishable from a broken one.

    A count that happens to match with no chip on screen is the failure this
    replaced: an unfiltered register that coincidentally had the same number of
    rows in it.
    """
    sign_in(page, base_url, HEAD)
    open_overview(page, base_url, scope)

    for target in drilldowns(page):
        if "/teemad/" not in target["href"]:
            continue
        page.goto(f"{base_url}{target['href']}")
        page.wait_for_load_state("networkidle")
        expect(page.locator(".filterchip")).not_to_have_count(0)


@pytest.mark.parametrize("scope", SCOPES)
def test_arriving_from_a_number_lands_on_the_rows(page, base_url, scope):
    """The reader clicked a count; they get the list, not the filter panel.

    A filtered register opens with a search box, a status strip and a narrowing
    panel that expands itself whenever a filter is active. Without the fragment
    the rows are below all of it.
    """
    sign_in(page, base_url, HEAD)
    open_overview(page, base_url, scope)

    for target in drilldowns(page):
        if "/teemad/" not in target["href"]:
            continue
        assert target["href"].endswith("#tulemused"), target["label"]
        page.goto(f"{base_url}{target['href']}")
        page.wait_for_load_state("networkidle")
        assert in_viewport(page, "#tulemused"), target["label"]
        assert page.evaluate("() => document.activeElement.id") == "tulemused", target["label"]


@pytest.mark.parametrize("scope", SCOPES)
def test_no_number_leads_nowhere(page, base_url, scope):
    sign_in(page, base_url, HEAD)
    open_overview(page, base_url, scope)

    for target in drilldowns(page):
        assert target["href"] != "#", target["label"]


# ---------------------------------------------------------------------------
# The destinations that are not the register
# ---------------------------------------------------------------------------


def test_the_unowned_areas_rail_lists_the_areas_and_not_the_files(page, base_url):
    """It counts policy areas, so it lists the areas — never the ownerless files.

    The count was a Seis figure on the Valdkonniti scope, opening the rail block
    below it. Since ADR 0049 the strip is one strip on both scopes — six figures
    about the department's work, none of them about areas — so what is asserted
    is the block itself: it lists areas, links each to that area's ownerless
    Matters, and never sends the reader to the whole ownerless register.
    """
    sign_in(page, base_url, HEAD)
    open_overview(page, base_url, "valdkonniti")

    block = page.locator("#vastutajata-valdkonnad")
    expect(block).to_be_visible()
    rows = block.locator(".railrow")
    if not rows.count():
        pytest.skip("every area with open work has an owner in the seeded world")

    first = rows.first
    href = first.get_attribute("href") or ""
    assert "valdkond=" in href, href
    assert "vastutaja=puudub" in href, href


def test_the_seven_day_opinion_figure_states_a_number_it_cannot_open(page, base_url):
    """The one figure on the strip that is deliberately not a link.

    It counts opinions sent in the last seven days, and the Arvamused workspace
    narrows by year and by month — so the only destination available holds more
    letters than the number beside it. An honest number beats a link to a
    different list, which is the same treatment the team table's three
    historical columns get (docs/adr/0049 §4, DS-24).
    """
    sign_in(page, base_url, HEAD)
    open_overview(page, base_url, "osakond")

    figure = page.locator(".seis__figure").filter(has_text="arvamust välja").first
    expect(figure).to_be_visible()
    assert figure.evaluate("node => node.tagName.toLowerCase()") == "span"
    assert figure.locator("a").count() == 0


def test_the_strip_carries_the_six_approved_figures(page, base_url):
    """Six, in the approved order, and the two that left it are elsewhere.

    «Arvamust koostamisel» and «esitatud arvamust <kuu>» were Ülevaade's, and
    the merged strip is the six states a head can act on this morning. Neither
    population was retired — `drafting_count` and the Arvamused workspace's own
    year/month filters still answer them — but neither is a figure on this page
    (docs/adr/0049 §4).
    """
    sign_in(page, base_url, HEAD)
    open_overview(page, base_url, "osakond")

    captions = [
        text.strip() for text in page.locator(".seis__figure .seis__caption").all_inner_texts()
    ]
    assert captions == [
        "üle tähtaja",
        "tähtaeg sel nädalal",
        "vastutajata",
        "uut läbi vaatamata",
        "järgmise tegevuseta",
        "arvamust välja · 7 p",
    ], captions


# ---------------------------------------------------------------------------
# The two lists this page shows in place rather than in the register
# ---------------------------------------------------------------------------


# The footer link this file used to follow — `.ovsection__more a`, which
# reloaded the page with a wider filter — is gone. The v2 design opens the rest
# of the list where the reader is standing, in a `details.pw-more` holding the
# remainder of the same list, so there is no second page load to assert
# (02-EKRAANID §B). The test that followed the link had begun skipping itself
# with a message about the seeded world, which was not what had happened.
#
# What the link proved — that the number and the rows behind it are one answer —
# is a property of the split rather than of a navigation now, and is asserted
# directly in `tests/test_overview_drilldowns.py::
# test_naita_veel_holds_the_remainder_of_the_same_list`.


def test_the_intervention_heading_opens_every_reason_at_once(page, base_url):
    """Four kinds of trouble in one list, and its link holds all four."""
    sign_in(page, base_url, HEAD)
    open_overview(page, base_url, "osakond")
    link = page.locator(".ovsection__head .ovsection__link").first
    claimed = first_number(link.inner_text())

    link.click()
    page.wait_for_load_state("networkidle")
    assert "too=sekkumist" in page.url
    assert shown_total(page) == claimed


def test_the_area_footer_opens_every_area_including_the_empty_ones(page, base_url):
    """A number of areas opens a list of areas — it opened the register."""
    sign_in(page, base_url, HEAD)
    open_overview(page, base_url, "valdkonniti")
    footer = page.locator(".ovsection__more a").first
    claimed = first_number(footer.inner_text())

    footer.click()
    page.wait_for_load_state("networkidle")
    assert "/teemad/" not in page.url
    assert page.locator(".arearow").count() == claimed


# ---------------------------------------------------------------------------
# Getting back out
# ---------------------------------------------------------------------------


def test_the_filter_a_figure_applied_can_be_cleared(page, base_url):
    """Arriving from a number must not be a dead end."""
    sign_in(page, base_url, HEAD)
    open_overview(page, base_url, "osakond")
    page.locator(".seis__figure").filter(has_text="järgmise tegevuseta").first.click()
    page.wait_for_load_state("networkidle")
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
    open_overview(page, base_url, "osakond")
    page.locator(".seis__figure").filter(has_text="järgmise tegevuseta").first.click()
    page.wait_for_load_state("networkidle")
    page.go_back()
    page.wait_for_load_state("networkidle")

    expect(page.locator(".seis__figure").first).to_be_visible()


def test_the_overdue_figure_opens_late_work_and_not_a_passed_review(page, base_url):
    """The figure counts late work; the register list it opens holds late work.

    Both a genuinely late DO and a WAIT past its review date exist in the seeded
    world, so a population that collected reviews as well as deadlines would
    fail here rather than merely be too generous (master specification 18.8).
    """
    from app.core.management.commands.seed_e2e_data import OVERDUE_TITLE, REVIEW_DUE_TITLE

    sign_in(page, base_url, HEAD)
    open_overview(page, base_url, "osakond")
    figure = page.locator(".seis__figure").filter(has_text="üle tähtaja").first
    claimed = int(figure.locator(".seis__number").inner_text().strip())

    figure.click()
    page.wait_for_load_state("networkidle")

    assert "too=hilinenud" in page.url
    assert shown_total(page) == claimed
    rows = page.locator(".table--register tbody tr")
    expect(rows.filter(has_text=OVERDUE_TITLE)).to_have_count(1)
    expect(rows.filter(has_text=REVIEW_DUE_TITLE)).to_have_count(0)


def test_the_stalled_figure_opens_the_matters_with_no_next_action(page, base_url):
    from app.core.management.commands.seed_e2e_data import UNASSIGNED_TITLE

    sign_in(page, base_url, HEAD)
    open_overview(page, base_url, "osakond")
    page.locator(".seis__figure").filter(has_text="järgmise tegevuseta").first.click()
    page.wait_for_load_state("networkidle")

    rows = page.locator(".table--register tbody tr")
    # The unassigned Matter has no next action either, so it is in this list —
    # what matters is that the list is filtered at all, which the chip proves.
    expect(rows.filter(has_text=UNASSIGNED_TITLE)).to_have_count(1)
