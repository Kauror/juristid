"""The standalone error page keeps a gutter on a phone (JUX-013, JUX-014).

`templates/error_page.html` is the page Django renders instead of a view — 400,
403, 404, 405, 500 and a refused CSRF check — and it is standalone on purpose,
with none of the application's shell. That also meant none of the shell's
spacing: `body` has no margin and `.page` no padding, so at 375 px the text
touched the left edge and the heading touched the top (JUX-014).

The page is reached here through a 403: a colleague without the right opening
the archive queue. Before JUX-013 that was Django's English page, so this also
checks the shape every error page shares: Estonian, one heading, a way home.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, sign_in

pytestmark = pytest.mark.e2e

GUTTER_PX = 16


@pytest.mark.parametrize("width", [320, 375, 1440])
def test_the_403_page_is_estonian_and_keeps_its_gutter(page, base_url, width):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": 800})
    response = page.goto(f"{base_url}/haldus/arvamuste-ulevaatus/")
    assert response is not None and response.status == 403

    expect(page.locator("html")).to_have_attribute("lang", "et")
    heading = page.locator("h1")
    expect(heading).to_have_count(1)
    expect(page.get_by_role("link", name="Avalehele")).to_have_attribute("href", "/")

    box = heading.bounding_box()
    assert box is not None
    assert box["x"] >= GUTTER_PX, f"the heading starts {box['x']}px from the left edge"
    assert box["y"] >= GUTTER_PX, f"the heading starts {box['y']}px from the top edge"
    right = width - (box["x"] + box["width"])
    assert right >= GUTTER_PX - 1, f"the heading ends {right}px from the right edge"
    assert page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth") <= 0
