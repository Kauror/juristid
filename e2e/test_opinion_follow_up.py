"""`Arvamuse järelkontroll`, the whole lawyer flow in a browser (docs/adr/0146).

Send an opinion → the check is planned 30 days out, naming its opinion → move
its day → check: no answer, next day → check again: the answer arrived → done.
A second opinion on the same Teema gets its own check, and ending its
monitoring asks why. At 375px nothing scrolls sideways.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    SANDRA,
    chronology,
    create_matter,
    document_overflows,
    open_add_panel,
    sign_in,
    wait_for_htmx,
)
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

PDF = b"%PDF-1.4 synthetic"
CHECK = "Kontrolli, kas adressaat on Koja arvamusele vastanud"


def _et(days: int) -> str:
    on = date.today() + timedelta(days=days)
    return f"{on.day}.{on.month}.{on.year}"


def _send_opinion(page, name: str) -> None:
    open_add_panel(page, "arvamus-koja")
    form = page.locator("#arvamus-koja")
    form.locator("input[name=upload]").set_input_files(
        [{"name": name, "mimeType": "application/pdf", "buffer": PDF + name.encode()}]
    )
    with page.expect_response(
        lambda r: r.url.endswith("/lisa/koja-arvamus/") and r.request.method == "POST"
    ) as caught:
        form.get_by_role("button", name="Salvesta", exact=True).click()
    assert caught.value.status == 200, caught.value.status
    wait_for_htmx(page)


#: A check's own row — planned `<li>` or current task line — and not its panels.
CHECK_ROW = "[id^=jarelkontroll-]:not([id*=tehtud]):not([id*=kuupaev])"


def _check_rows(page):
    return page.locator(f"#praegune-tegevus {CHECK_ROW}")


def _save(page, row, endpoint: str) -> None:
    with page.expect_response(
        lambda r: (
            "/jarelkontroll/" in r.url
            and r.url.endswith(f"/{endpoint}/")
            and r.request.method == "POST"
        )
    ) as caught:
        row.get_by_role("button", name="Salvesta", exact=True).last.click()
    assert caught.value.status == 200, caught.value.status
    wait_for_htmx(page)


def _done(page, row, outcome: str, *, body: str = "", next_day: int | None = None) -> None:
    row.locator("details > summary", has_text="Tehtud").click()
    row.get_by_label(outcome, exact=True).check()
    if next_day is not None:
        row.locator("input[name=next_check_on]").fill(_et(next_day))
    if body:
        row.locator("textarea[name=body]").fill(body)
    _save(page, row, "tehtud")


def test_send_check_move_no_answer_check_again_answered(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(
        page,
        base_url,
        unique_title("Järelkontroll"),
        owner=SANDRA,
        sender="Näidisministeerium",
    )

    _send_opinion(page, "arvamus.pdf")

    rows = _check_rows(page)
    expect(rows).to_have_count(1)
    row = rows.first
    expect(row).to_contain_text(CHECK)
    expect(row).to_contain_text(_et(30))
    expect(row.locator(".followup__subject")).to_contain_text(f"Koja arvamus {_et(0)}")
    expect(row.locator(".followup__subject")).to_contain_text("Näidisministeerium")
    expect(row.locator(".curact__dismiss")).to_have_count(0)
    # The send reads as one row, with its check under it.
    sent = chronology(page).locator(".uxtl__item", has_text="Arvamus välja")
    expect(sent).to_have_count(1)
    expect(sent).to_contain_text(CHECK)

    # Move its day — earlier, in place.
    row.locator("details > summary", has_text="Muuda").click()
    row.locator("input[name=target_date]").fill(_et(20))
    _save(page, row, "kuupaev")
    row = _check_rows(page).first
    expect(row).to_contain_text(_et(20))

    # Checked: no answer yet; the next check on a day the lawyer chooses.
    _done(page, row, "Vastust ei ole — kontrollin uuesti", next_day=40, body="Helistasin.")
    rows = _check_rows(page)
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text(_et(40))
    expect(chronology(page)).to_contain_text("Vastust ei ole — kontrollin uuesti")

    # Checked again: the answer arrived. Nothing new is planned.
    _done(page, rows.first, "Vastus saabunud", body="Ministeerium vastas kirjaga.")
    expect(_check_rows(page)).to_have_count(0)
    expect(chronology(page)).to_contain_text("Vastus saabunud. Ministeerium vastas kirjaga.")

    # And it is not on Minu asjad any more.
    page.goto(f"{base_url}/minu-asjad/")
    expect(page.locator("body")).not_to_contain_text(CHECK)


def test_a_second_opinion_keeps_its_own_check_and_ending_asks_why(page, base_url):
    sign_in(page, base_url, SANDRA)
    url = create_matter(
        page,
        base_url,
        unique_title("Kaks arvamust"),
        owner=SANDRA,
        sender="Näidisministeerium",
    )
    _send_opinion(page, "esimene.pdf")
    _send_opinion(page, "teine.pdf")

    rows = _check_rows(page)
    expect(rows).to_have_count(2)

    first = rows.first
    first.locator("details > summary", has_text="Tehtud").click()
    first.get_by_label("Lõpetan jälgimise", exact=True).check()
    with page.expect_response(
        lambda r: "/jarelkontroll/" in r.url and r.request.method == "POST"
    ) as caught:
        first.get_by_role("button", name="Salvesta", exact=True).last.click()
    assert caught.value.status == 400
    wait_for_htmx(page)
    refused = _check_rows(page).first
    expect(refused).to_contain_text("Kirjuta, miks jälgimine lõpeb.")

    refused.get_by_label("Lõpetan jälgimise", exact=True).check()
    refused.locator("textarea[name=body]").fill("Eelnõu võeti menetlusest tagasi.")
    _save(page, refused, "tehtud")

    expect(_check_rows(page)).to_have_count(1)
    page.goto(f"{base_url}/minu-asjad/")
    expect(page.get_by_text(CHECK)).to_have_count(1)
    page.goto(url)
    expect(_check_rows(page)).to_have_count(1)


def test_the_check_row_fits_a_phone(page, base_url):
    sign_in(page, base_url, SANDRA)
    url = create_matter(
        page, base_url, unique_title("Telefonis"), owner=SANDRA, sender="Näidisministeerium"
    )
    _send_opinion(page, "arvamus.pdf")

    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(url)
    row = _check_rows(page).first
    row.locator("details > summary", has_text="Tehtud").click()

    expect(row.get_by_label("Vastus saabunud", exact=True)).to_be_visible()
    assert not document_overflows(page)
    assert re.search(r"\d+\.\d+\.\d{4}", row.inner_text())
