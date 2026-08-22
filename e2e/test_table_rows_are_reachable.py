"""Every table's first row is actually clickable, on every table.

Stage 2H.2 found a sticky column header sitting on top of the first row of a
list: `.tablewrap` scrolls horizontally, which makes it a scroll container in
both axes, so a header offset for the sticky top bar slid down over row one.

The reason it survived review is the reason this file exists. A full-page
screenshot renders sticky elements unshifted, so every committed baseline
looked right; the visual suite compared them and agreed. Only a click found it,
and only on the one page a test happened to click.

So the guard is geometric and applies to every table the seeded world renders,
rather than to the one page that caught it. A header that overlaps its own first
row is the defect, whatever CSS produced it.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import ADMIN, MARTIN, sign_in

pytestmark = pytest.mark.e2e

#: Every route in the seeded world that renders a table, and who may read it.
#: Kept as data so a new list surface is one line rather than a new test.
TABLE_PAGES: list[tuple[str, str]] = [
    ("martin", "/teemad/?olek=koik"),
    ("martin", "/minu-too/"),
    ("martin", "/saabunud/"),
    ("martin", "/statistika/arvamused/"),
    ("martin", "/statistika/materjalid/"),
    ("admin", "/haldus/arvamuste-arhiiv/"),
]

OVERLAP_TOLERANCE = 0.5


def _box(locator):
    box = locator.bounding_box()
    assert box is not None, "element has no layout box"
    return box


@pytest.mark.parametrize(("persona", "route"), TABLE_PAGES, ids=lambda value: str(value))
def test_no_sticky_header_covers_its_own_first_row(page, base_url, persona, route):
    sign_in(page, base_url, ADMIN if persona == "admin" else MARTIN)
    page.goto(f"{base_url}{route}")
    page.wait_for_load_state("networkidle")

    tables = page.locator(".tablewrap table")
    examined = 0
    for index in range(tables.count()):
        table = tables.nth(index)
        first_row = table.locator("tbody tr").first
        if not first_row.count():
            continue
        header = table.locator("thead th").first
        expect(first_row).to_be_visible()
        examined += 1

        header_box = _box(header)
        row_box = _box(first_row)
        assert header_box["y"] + header_box["height"] <= row_box["y"] + OVERLAP_TOLERANCE, (
            f"{route}: the column header overlaps the first row by "
            f"{header_box['y'] + header_box['height'] - row_box['y']:.1f}px — "
            "a link in that row cannot be clicked"
        )

    # Guards the guard. Every assertion above is inside a loop, so a route that
    # stopped rendering a populated table — a renamed wrapper class, a seed that
    # no longer fills it — would turn this test green by examining nothing.
    assert examined, f"{route}: no populated table was found, so nothing was checked"


@pytest.mark.parametrize(("persona", "route"), TABLE_PAGES, ids=lambda value: str(value))
def test_the_first_rows_own_links_receive_the_pointer(page, base_url, persona, route):
    """Geometry is not the only way to cover a control.

    A transparent overlay, a wide sticky cell or a mis-stacked disclosure all
    leave the boxes where they are and still swallow the click, so the pointer
    is asked directly: at the middle of the link, is the link what the browser
    would hit?
    """
    sign_in(page, base_url, ADMIN if persona == "admin" else MARTIN)
    page.goto(f"{base_url}{route}")
    page.wait_for_load_state("networkidle")

    links = page.locator(".tablewrap table tbody tr:first-child a")
    assert links.count(), f"{route}: no first-row link was found, so nothing was checked"
    for index in range(links.count()):
        link = links.nth(index)
        if not link.is_visible():
            continue
        blocker = link.evaluate(
            """(element) => {
                const box = element.getBoundingClientRect();
                const hit = document.elementFromPoint(
                    box.left + box.width / 2, box.top + box.height / 2
                );
                if (!hit) return 'nothing';
                return element.contains(hit) || hit.contains(element)
                    ? '' : hit.tagName + '.' + (hit.className || '');
            }"""
        )
        assert blocker == "", f"{route}: {blocker} intercepts the pointer over a first-row link"
