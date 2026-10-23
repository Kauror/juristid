"""Two consultation rounds on one Teema, in a real browser (owner brief §6, D1–D3).

The VTK's request is answered by the first `Koja arvamus`; then the draft bill
arrives with a new `Arvamuse tähtaeg` — already two days late when recorded —
through `+ Lisa → Arvamuse tähtaeg`. What a lawyer sees:

* the header states the new deadline as late, never «vastatud»;
* the rail's `Arvamuse tähtajad` keeps the first request, answered, beside the
  current one;
* `Teema käik` keeps the first opinion's «Vastatud · Arvamuse tähtaeg …» line;
* Minu asjad lists the Teema under «Üle tähtaja»;
* the second opinion, naming the new request, ends it everywhere, and the first
  answer is still where it was.

The service-level proof of the same scenario, and of every surface agreeing, is
``tests/test_consultation_rounds.py``. The suite reads state off the page only
(e2e/conftest.py).
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, chronology, open_add_panel, sign_in, wait_for_htmx
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

PDF = b"%PDF-1.4 synthetic consultation round"
MINISTRY = "Näidisministeerium"


def _et(days: int) -> str:
    on = date.today() + timedelta(days=days)
    return f"{on.day}.{on.month}.{on.year}"


def _send_opinion_answering_the_deadline(page, file_name: str) -> None:
    open_add_panel(page, "arvamus-koja")
    form = page.locator("#arvamus-koja")
    form.locator("input[name=upload]").set_input_files(
        {"name": file_name, "mimeType": "application/pdf", "buffer": PDF}
    )
    form.locator("[name=sent_on]").fill(_et(0))
    form.get_by_role("checkbox", name=re.compile("^Vastab arvamuse küsimisele")).check()
    with page.expect_response(
        lambda r: r.url.endswith("/lisa/koja-arvamus/") and r.request.method == "POST"
    ):
        form.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)


def _file_the_vtk(page, base_url: str, title: str, deadline: str) -> str:
    """`Uus teema` from the ministry with the first request's deadline.

    The sender is chosen because a `Koja arvamus` is addressed to it.
    """
    page.goto(f"{base_url}/teemad/uus/")
    page.fill("#id_title", title)
    page.locator("#saatja-otsi").click()
    page.locator("#saatja-otsi").type(MINISTRY[:8], delay=20)
    page.locator("#saatja-tulemused").get_by_text(MINISTRY, exact=True).click()
    page.fill("#id_response_deadline", deadline)
    page.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    return page.url


def _settled_deadline(page):
    return page.locator("#teema-pais .metaline__value--settled").first


def _overdue_band(page):
    return page.locator("section#ule_tahtaja")


def test_a_second_request_after_an_answered_first_is_late_and_the_first_stays_answered(
    page, base_url
):
    sign_in(page, base_url, SANDRA)
    title = unique_title("Kaks konsultatsiooniringi")
    url = _file_the_vtk(page, base_url, title, _et(5))

    # Round 1: the VTK's request is answered by name.
    _send_opinion_answering_the_deadline(page, "arvamus-vtk.pdf")
    page.goto(url)
    expect(_settled_deadline(page)).to_contain_text(f"{_et(5)} · vastatud")

    # Round 2: the draft bill, asked again, recorded two days after its day.
    page.goto(url)
    open_add_panel(page, "marge-arvamuse-tahtaeg")
    page.locator("#id_response_deadline_date").fill(_et(-2))
    page.locator("#marge-arvamuse-tahtaeg button[type=submit]").click()
    wait_for_htmx(page)

    page.goto(url)
    deadline = page.locator("#teema-pais .metaline__value--deadline").first
    expect(deadline).to_contain_text(f"{_et(-2)} · 2 p üle")
    expect(deadline).not_to_contain_text(re.compile("vastatud", re.IGNORECASE))
    rail = page.locator("#arvamuse-tahtajad")
    expect(rail).to_contain_text(f"{_et(-2)} · praegune")
    expect(rail).to_contain_text(f"{_et(5)} · vastatud")
    first = chronology(page).locator(".uxtl__item", has_text="Vastatud")
    expect(first).to_have_count(1)
    expect(first).to_contain_text(f"Arvamuse tähtaeg {_et(5)}")

    page.goto(f"{base_url}/minu-asjad/")
    expect(_overdue_band(page)).to_contain_text(title)

    # The second opinion answers the second request — and only that.
    page.goto(url)
    _send_opinion_answering_the_deadline(page, "arvamus-eelnou.pdf")
    page.goto(url)
    expect(page.locator("#teema-pais")).not_to_contain_text("p üle")
    rail = page.locator("#arvamuse-tahtajad")
    expect(rail).to_contain_text(f"{_et(-2)} · vastatud")
    expect(rail).to_contain_text(f"{_et(5)} · vastatud")
    answered = chronology(page).locator(".uxtl__item", has_text="Vastatud")
    expect(answered).to_have_count(2)
    expect(answered.filter(has_text=f"Arvamuse tähtaeg {_et(5)}")).to_have_count(1)
    expect(answered.filter(has_text=f"Arvamuse tähtaeg {_et(-2)}")).to_have_count(1)

    page.goto(f"{base_url}/minu-asjad/")
    overdue = _overdue_band(page)
    if overdue.count():
        expect(overdue).not_to_contain_text(title)
