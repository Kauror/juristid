"""Uuendused → Lovable in a real browser (docs/adr/0192 §11).

Two synthetic nights are planted in the server's own database
(`e2e/lovable_review_plant.py`); the page compares them. What a DOM assertion
in the Django suite cannot see is checked here: the section tabs, the open
newest night, the change table, and that at a phone's width the table scrolls
inside its own box while the document never scrolls sideways. Screenshots of
both sections at both widths go to the CI artifact for the pull request.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest
from playwright.sync_api import expect

from e2e.conftest import REPOSITORY_ROOT, SANDRA, document_overflows, sign_in


@pytest.fixture(scope="module")
def planted():
    result = subprocess.run(
        [
            sys.executable,
            "manage.py",
            "shell",
            "-c",
            "exec(open('e2e/lovable_review_plant.py', encoding='utf-8').read())",
        ],
        cwd=REPOSITORY_ROOT,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, "\n".join(["stdout:", result.stdout, "stderr:", result.stderr])


@pytest.mark.parametrize("width", [1440, 375])
def test_the_lovable_section_shows_the_night_at_every_width(
    page, base_url, planted, screenshots, width
):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{base_url}/uuendused/lovable/")
    page.wait_for_load_state("networkidle")

    expect(page.get_by_role("link", name="Lovable", exact=True)).to_have_attribute(
        "aria-current", "page"
    )
    newest = page.locator("details.relnotes__day").first
    expect(newest).to_have_attribute("open", "")
    expect(newest.locator(".accordion__summary")).to_contain_text("3 teemat muudetud")
    table = newest.locator("table.lovnight__table").first
    expect(table).to_be_visible()
    for word in ("Pealkiri", "Uuendatud", "Vajab ülevaatamist", "Vastuolu"):
        expect(newest).to_contain_text(word)
    expect(newest.get_by_role("link").first).to_have_attribute(
        "href", re.compile(r"/teemad/[0-9a-f-]{36}/")
    )
    assert not document_overflows(page), "the document scrolls sideways"
    # The «Juristides» answer is on screen without a sideways swipe at any width.
    verdict = newest.locator(".statechip", has_text="Vastuolu").first
    box, wrap = verdict.bounding_box(), table.locator("xpath=..").bounding_box()
    assert box and wrap and box["x"] + box["width"] <= wrap["x"] + wrap["width"] + 1
    screenshots(page, f"uuendused-lovable-{width}")


@pytest.mark.parametrize("width", [1440, 375])
def test_the_juristid_section_is_the_default(page, base_url, screenshots, width):
    sign_in(page, base_url, SANDRA)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{base_url}/uuendused/")
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("link", name="Juristid", exact=True)).to_have_attribute(
        "aria-current", "page"
    )
    expect(page.locator("details.relnotes__day").first).to_have_attribute("open", "")
    assert not document_overflows(page), "the document scrolls sideways"
    screenshots(page, f"uuendused-juristid-{width}")
