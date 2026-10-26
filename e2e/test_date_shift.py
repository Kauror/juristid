"""«Nihuta kuupäeva» in a real browser (docs/adr/0143, amendment of 2026-10-26).

A Teema of its own, so the seeded rows other files read keep their days: the
step is dated a week out, moved by a week from its row's `⋯` menu, and the
page comes back on the same desk saying what changed. Choosing a move only
previews it; `Loobu` writes nothing.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from app.core.dates import format_estonian_date
from e2e.conftest import MARTIN, create_matter, sign_in, start_first_step, unique_title

pytestmark = pytest.mark.e2e


def _row(page, text):
    return page.locator("[data-workrow]", has_text=text)


def _open_shift(page, row):
    row.locator(".rowmenu__trigger").click()
    row.get_by_text("Nihuta kuupäeva", exact=True).click()
    return row.locator("form.dateshift")


def test_a_step_is_moved_by_a_week_after_a_preview(page, base_url):
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": 1440, "height": 1000})
    step = unique_title("Nihutatav samm")
    create_matter(page, base_url, unique_title("Nihutamise teema"), owner=MARTIN)
    start_first_step(page, days=7, text=step)
    page.goto(f"{base_url}/minu-asjad/?kalender=kuu")

    old = date.today() + timedelta(days=7)
    new = old + timedelta(days=7)
    form = _open_shift(page, _row(page, step))
    expect(form).to_be_visible()
    expect(form.locator(".dateshift__current")).to_contain_text(format_estonian_date(old))

    # Loobu writes nothing and folds the control away.
    form.get_by_text("+1 päev").click()
    form.get_by_role("button", name="Loobu").click()
    expect(form).to_be_hidden()

    form = _open_shift(page, _row(page, step))
    form.get_by_text("+1 nädal").click()
    expect(form.locator(".dateshift__preview")).to_contain_text(format_estonian_date(new))
    form.get_by_role("button", name="Salvesta").click()

    page.wait_for_url(re.compile(r"/minu-asjad/\?kalender=kuu$"))
    expect(page.locator(".messages")).to_contain_text(
        f"Kuupäev muudetud: {old.day}.{old.month} → {new.day}.{new.month}."
    )
    expect(_row(page, step)).to_have_count(1)
