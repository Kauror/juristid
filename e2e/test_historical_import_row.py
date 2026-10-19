"""Imported historical files read as one row per import, at any width.

The seeded Teema carries two files an import brought (`seed_e2e_data`: a
signed container and a text file, both recorded by `LegacySourceResourceImport`).
They used to draw two «lisas dokumendi» rows dated the import day; they are one
row now, named as an import, and opening it reaches both files
(docs/adr/0092 §6, amendment of 2026-10-19).
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from app.core.management.commands.seed_e2e_data import (
    HISTORICAL_FILENAME,
    HISTORICAL_INLINE_FILENAME,
    HISTORICAL_PAGE_TITLE,
    OPEN_TITLE,
)
from e2e.conftest import KAIK_ROW, MARTIN, sign_in

pytestmark = pytest.mark.e2e

HEADLINE = "Ajaloolised dokumendid imporditud"


@pytest.mark.parametrize("width", [375, 1440])
def test_one_imports_files_are_one_row_that_opens_onto_them(page, base_url, width):
    page.set_viewport_size({"width": width, "height": 900})
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teemad/?olek=koik")
    page.get_by_role("link", name=re.compile(re.escape(OPEN_TITLE))).first.click()

    rows = page.locator(KAIK_ROW)
    imported = rows.filter(has_text=HEADLINE)
    expect(imported).to_have_count(1)
    expect(rows.filter(has_text="lisas dokumendi")).to_have_count(0)

    imported.get_by_role("button", name=re.compile(HEADLINE)).click()
    expect(
        imported.get_by_text(
            re.compile(r"2 faili · OneNote: .*" + re.escape(HISTORICAL_PAGE_TITLE))
        )
    ).to_be_visible()
    for name in (HISTORICAL_FILENAME, HISTORICAL_INLINE_FILENAME):
        expect(imported.get_by_role("link", name=re.compile(re.escape(name)))).to_be_visible()

    # Nothing pushes the page sideways on a phone.
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
