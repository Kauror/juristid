"""A click on a `Menetluse kulg` step dates it, in the browser (owner, 2026-10-26).

The Python suite holds the rules (`tests/test_rail_step_date.py`); this is the
half a lawyer meets: the step's name is a button, it opens the small `millal?`
panel under the rail, `Kuu` and `Aasta` save as *märts 2027* on the rail
without a reload, and `Loobu` closes the panel, writes nothing and puts focus
back on the step.

Files its own Matter: it writes Matter-level rows, and the seeded world is
shared across a shard.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, create_matter, sign_in, unique_title

pytestmark = pytest.mark.e2e


def _save(page, panel) -> None:
    with page.expect_response(
        lambda r: "/menetluse-kulg/" in r.url and r.request.method == "POST"
    ) as caught:
        panel.get_by_role("button", name="Salvesta").click()
    assert caught.value.status == 200
    page.wait_for_load_state("networkidle")


def test_a_step_is_dated_by_clicking_it(page, base_url):
    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("QA kuupäev rajal"), owner=SANDRA)
    page.goto(url)
    page.wait_for_load_state("networkidle")
    # An undated step, through `Muuda` as before.
    page.locator(".lprail__edit").click()
    panel = page.locator("#menetluse-kulg-muuda")
    panel.get_by_text("+ Lisa samm", exact=True).click()
    page.fill("input[name='uus__title']", "Komisjoni istung")
    _save(page, panel)

    step = page.get_by_role("button", name="Komisjoni istung: lisa kuupäev")
    expect(step).to_be_visible()
    step.click()
    expect(panel).to_contain_text("Komisjoni istung — millal?")

    # `Loobu` writes nothing and gives focus back to the step.
    panel.get_by_role("button", name="Loobu").click()
    expect(panel.locator("form")).to_have_count(0)
    expect(step).to_be_focused()

    step.click()
    panel.locator("label.precision__chip", has_text="Kuu").click()
    panel.locator("select[name='kp_month']").select_option("3")
    panel.locator("input[name='kp_year']").fill("2027")
    _save(page, panel)

    rail = page.locator(".lprail .tl-strip")
    expect(rail).to_contain_text("märts 2027")
    expect(
        page.get_by_role("button", name="Komisjoni istung: muuda kuupäeva (märts 2027)")
    ).to_be_visible()
    page.reload()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".lprail .tl-strip")).to_contain_text("märts 2027")


def test_the_panel_opens_on_today_and_follows_a_typed_day(page, base_url):
    """Owner's UX review of 2026-10-26: an undated step opens on Tallinn's today
    in all four `Täpsus` controls; the day typed last carries into `Kuu`,
    `Kvartal` and `Aasta` — in each format the server accepts — and switching
    a chip changes no value."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    sign_in(page, base_url, SANDRA)
    url = create_matter(page, base_url, unique_title("QA kuupäev täna"), owner=SANDRA)
    page.goto(url)
    page.wait_for_load_state("networkidle")
    page.locator(".lprail__edit").click()
    panel = page.locator("#menetluse-kulg-muuda")
    panel.get_by_text("+ Lisa samm", exact=True).click()
    page.fill("input[name='uus__title']", "Istung täna")
    _save(page, panel)

    page.get_by_role("button", name="Istung täna: lisa kuupäev").click()
    today = datetime.now(ZoneInfo("Europe/Tallinn")).date()
    day = panel.locator("input[name='kp_date']")
    month = panel.locator("select[name='kp_month']")
    quarter = panel.locator("select[name='kp_quarter']")
    year = panel.locator("input[name='kp_year']")
    expect(day).to_have_value(f"{today.day:02d}.{today.month:02d}.{today.year}")
    expect(month).to_have_value(str(today.month))
    expect(quarter).to_have_value(str((today.month - 1) // 3 + 1))
    expect(year).to_have_value(str(today.year))

    for typed, m, q, y in (
        ("15.02.2029", "2", "1", "2029"),
        ("5.8.27", "8", "3", "2027"),
        ("2028-11-03", "11", "4", "2028"),
    ):
        day.fill(typed)
        day.dispatch_event("change")
        expect(month).to_have_value(m)
        expect(quarter).to_have_value(q)
        expect(year).to_have_value(y)

    panel.locator("label.precision__chip", has_text="Kuu").click()
    expect(month).to_have_value("11")
    _save(page, panel)
    expect(page.locator(".lprail .tl-strip")).to_contain_text("november 2028")
