"""`Lõpeta teema`, in a real browser (docs/adr/0131, amendment of 2026-10-25).

The domain suite proves the rules (`tests/test_lopeta_teema.py`); this proves a
person can do it:

    open Matter → Lõpeta teema → Põhjus → (Selgitus) → Lõpeta teema → closed

— with the reason already chosen, by keyboard as well as by mouse, without a
reload, the header, banner, workspace and `Teema käik` moving together, the
`Hetkeseis` left where it was, and live work named and confirmed first.

Everything here is synthetic.
"""

from __future__ import annotations

import uuid

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    MARTIN,
    create_matter,
    open_add_panel,
    sign_in,
    start_first_step,
    wait_for_htmx,
)

pytestmark = pytest.mark.e2e


def _title(what: str) -> str:
    """Unique per run, so `Uus teema` never answers a re-run with «sarnane teema»."""
    return f"Lõpeta teema brauserikatse: {what} {uuid.uuid4().hex[:6]}"


EXPLANATION = "Kohtusime ministeeriumiga kaks korda; ministeerium loobus eelnõust."


def _press_close(page) -> int:
    """`Lõpeta teema`'s own save, and the server's status for it."""
    with page.expect_response(
        lambda r: r.request.method == "POST" and "/lisa/lopeta/" in r.url
    ) as answer:
        page.locator("#teema-lopeta button[type=submit]").click()
    wait_for_htmx(page)
    return answer.value.status


def test_a_file_closes_with_its_reason_already_chosen(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, _title("lihtne"), stage="Riigikogus")
    expect(page.locator(".badge--state")).to_contain_text("Aktiivne")

    open_add_panel(page, "teema-lopeta")
    panel = page.locator("#teema-lopeta")
    expect(panel.get_by_role("radio", name="Koda ei tegele edasi", exact=True)).to_be_checked()
    panel.locator("[name=explanation]").fill(EXPLANATION)

    assert _press_close(page) == 200

    # One swap: header badge, banner, workspace and history, no reload.
    expect(page.locator(".badge--state")).to_contain_text("Lõpetatud")
    banner = page.locator(".banner--closed")
    expect(banner).to_contain_text("Koda ei tegele edasi")
    expect(banner).to_contain_text(EXPLANATION)
    expect(page.locator("#lisa-teemale")).to_have_count(0)
    history = page.locator("#ajalugu-loend")
    expect(history).to_contain_text("Teema suletud")
    expect(history).to_contain_text(EXPLANATION)
    # The external process did not move.
    expect(page.locator("#teema-hetkeseis")).to_contain_text("Riigikogus")
    # `Ava uuesti` is offered on the closed banner.
    expect(banner.get_by_role("button", name="Ava uuesti")).to_be_visible()


def test_the_panel_opens_and_saves_from_the_keyboard(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, _title("klaviatuur"), stage="Idee")

    page.locator("#teema-lopeta-valik").focus()
    page.keyboard.press("Space")
    panel = page.locator("#teema-lopeta")
    expect(panel).to_be_visible()
    reason = panel.get_by_role("radio", name="Eelnõu või algatus lõpetati", exact=True)
    reason.focus()
    page.keyboard.press("Space")
    expect(reason).to_be_checked()
    with page.expect_response(lambda r: "/lisa/lopeta/" in r.url) as answer:
        panel.locator("[name=explanation]").focus()
        page.keyboard.press("Tab")
        page.keyboard.press("Enter")
    assert answer.value.status == 200
    wait_for_htmx(page)
    expect(page.locator(".banner--closed")).to_contain_text("Eelnõu või algatus lõpetati")


def test_live_work_is_named_and_closes_once_confirmed(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_matter(page, base_url, _title("pooleli töö"), stage="Riigikogus")
    start_first_step(page)

    open_add_panel(page, "teema-lopeta")
    page.locator("#teema-lopeta [name=explanation]").fill(EXPLANATION)
    assert _press_close(page) == 400

    panel = page.locator("#teema-lopeta")
    expect(panel).to_be_visible()
    expect(panel).to_contain_text("praegune tegevus")
    # What was typed is still there.
    expect(panel.locator("[name=explanation]")).to_have_value(EXPLANATION)
    confirm = panel.get_by_label("Sulge teema ja lõpeta pooleli töö")
    expect(confirm).not_to_be_checked()
    expect(page.locator(".badge--state")).to_contain_text("Aktiivne")

    confirm.check()
    assert _press_close(page) == 200

    expect(page.locator(".badge--state")).to_contain_text("Lõpetatud")
    expect(page.locator("#praegune-tegevus .curact__form")).to_have_count(0)
