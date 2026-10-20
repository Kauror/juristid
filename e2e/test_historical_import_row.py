"""Imported historical files read on the day they were added to OneNote, at any width.

The seeded Teema carries two files an import brought (`seed_e2e_data`): a
signed container whose source records when it was added to OneNote
(10.6.2019, `HISTORICAL_ADDED_AT`), and a text file whose source records no
time. They draw two honest rows — «Dokument lisatud OneNote'i · 10.6.2019» and
«OneNote'i dokument — lisamise kuupäev teadmata» — and never a «lisas
dokumendi» row dated the import (docs/adr/0092 §6, amendment of 2026-10-20).
Opening each row reaches its file.
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

DATED = "Dokument lisatud OneNote'i"
UNDATED = "OneNote'i dokument — lisamise kuupäev teadmata"


@pytest.mark.parametrize("width", [375, 1440])
def test_each_file_reads_on_its_onenote_day_and_opens_onto_it(page, base_url, width):
    page.set_viewport_size({"width": width, "height": 900})
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teemad/?olek=koik")
    page.get_by_role("link", name=re.compile(re.escape(OPEN_TITLE))).first.click()

    rows = page.locator(KAIK_ROW)
    expect(rows.filter(has_text="lisas dokumendi")).to_have_count(0)

    dated = rows.filter(has_text=DATED)
    expect(dated).to_have_count(1)
    expect(dated).to_contain_text("10.6.2019")
    dated.get_by_role("button", name=re.compile(re.escape(DATED))).click()
    # The seeded page was created when the world was seeded, after its 2019 file:
    # the row says the file reached OneNote before the page it is on now.
    expect(
        dated.get_by_text(
            re.compile(
                r"1 fail · OneNote: .*"
                + re.escape(HISTORICAL_PAGE_TITLE)
                + r" · lisatud enne praeguse lehe loomist"
            )
        )
    ).to_be_visible()
    expect(
        dated.get_by_role("link", name=re.compile(re.escape(HISTORICAL_FILENAME)))
    ).to_be_visible()

    undated = rows.filter(has_text=UNDATED)
    expect(undated).to_have_count(1)
    expect(undated).to_contain_text("kuupäev teadmata")
    undated.get_by_role("button", name=re.compile(re.escape(UNDATED))).click()
    expect(
        undated.get_by_role("link", name=re.compile(re.escape(HISTORICAL_INLINE_FILENAME)))
    ).to_be_visible()

    # Nothing pushes the page sideways on a phone.
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
