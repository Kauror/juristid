"""A refusal stays on its row, and the header agrees with the save, in a real browser.

Three answers to one save that used to spread across the page (ENG-036,
ENG-091, ENG-093):

* a refused `+ Lisa fail` on one `Märge` or `Väline seisukoht` drew the picker,
  one shared id and the error on **every** such row, and the sentence under
  PRAEGUNE TEGEVUS as well;
* a refused `Salvesta avaldatuna` came back with Django's default ids, beside
  the other planned rows' own;
* a `Märge` that moved `Hetkeseis` left the header saying the old stage.

Each test ends with the same check a person cannot make by eye: no id on the page
belongs to two elements.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import (
    MARTIN,
    create_matter,
    open_add_panel,
    set_next_step,
    sign_in,
    wait_for_htmx,
)
from e2e.test_website_overview import _plan_through_the_service
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

MINISTRY = "Näidisministeerium"
REFUSAL = "Vali vähemalt üks fail."


def _when(days: int) -> str:
    day = date.today() + timedelta(days=days)
    return f"{day.day}.{day.month}.{day.year}"


def _pdf(name: str) -> dict:
    return {"name": name, "mimeType": "application/pdf", "buffer": b"%PDF-1.4 synthetic evidence"}


def _duplicate_ids(page) -> list[str]:
    return page.evaluate(
        """() => {
          const seen = new Map();
          document.querySelectorAll('[id]').forEach(el => {
            seen.set(el.id, (seen.get(el.id) || 0) + 1);
          });
          return [...seen].filter(([, n]) => n > 1).map(([id]) => id);
        }"""
    )


def _row(page, text: str):
    return page.locator("article.uxtl__item").filter(has_text=text)


def _pinned(page, row):
    """The same row by its record's own region id, whatever its text reads now.

    A position's picker names the institution rather than the summary, so a
    text filter stops matching the row the moment the picker opens.
    """
    region = row.locator("[id$='-sisu']").first.get_attribute("id")
    return page.locator(f"article.uxtl__item:has([id='{region}'])")


def _teema_with_a_step(page, base_url: str) -> str:
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, unique_title("Rea keeldumine"))
    set_next_step(page, "Loe eelnõu läbi", _when(5))
    wait_for_htmx(page)
    return url


def _file_marge(page, title: str) -> None:
    open_add_panel(page, "marge-tavaline")
    page.fill("#id_marge_title", title)
    page.locator("#marge-tavaline button[type=submit]").click()
    wait_for_htmx(page)
    _row(page, title).first.wait_for()


def _file_position(page, summary: str) -> None:
    open_add_panel(page, "arvamus-teiste")
    box = page.locator("#valine-seisukoht-otsi")
    box.click()
    box.fill("")
    box.type(MINISTRY[:8], delay=20)
    page.locator("#valine-seisukoht-tulemused").get_by_role(
        "option", name=MINISTRY, exact=True
    ).click()
    page.locator("#arvamus-teiste [name=summary]").fill(summary)
    page.locator("#arvamus-teiste").get_by_role("button", name="Salvesta").click()
    wait_for_htmx(page)
    _row(page, summary).first.wait_for()


def _refuse_an_empty_picker_on(page, row) -> None:
    row.locator("button.uxtl__edit[id$='-toend']").click()
    wait_for_htmx(page)
    row.locator("form.uxtl__editform").get_by_role("button", name="Lisa fail", exact=True).click()
    wait_for_htmx(page)


def _assert_refusal_is_only_on(page, row, others) -> None:
    pickers = page.locator("form.uxtl__editform input[type=file]")
    expect(pickers).to_have_count(1)
    expect(row.locator("form.uxtl__editform input[type=file]")).to_have_count(1)
    for other in others:
        expect(other.locator("form.uxtl__editform")).to_have_count(0)
    expect(page.get_by_text(REFUSAL)).to_have_count(1)
    expect(row.get_by_text(REFUSAL)).to_have_count(1)
    expect(page.locator("#praegune-tegevus")).not_to_contain_text(REFUSAL)
    # The sentence is the control's own description, not a paragraph beside it.
    described = pickers.first.get_attribute("aria-describedby") or ""
    assert described, "the refused picker describes nothing"
    assert any(
        REFUSAL in (page.locator(f"[id='{ref}']").text_content() or "")
        for ref in described.split()
        if page.locator(f"[id='{ref}']").count()
    ), described
    assert _duplicate_ids(page) == []


def test_a_refused_marge_file_stays_on_its_own_row(page, base_url):
    _teema_with_a_step(page, base_url)
    _file_marge(page, "Esimene samm menetluses")
    _file_marge(page, "Teine samm menetluses")
    first = _pinned(page, _row(page, "Esimene samm menetluses"))
    second = _pinned(page, _row(page, "Teine samm menetluses"))

    _refuse_an_empty_picker_on(page, second)
    _assert_refusal_is_only_on(page, second, [first])

    # The retry posts to the row it is on, and the file lands there.
    second.locator("form.uxtl__editform input[type=file]").set_input_files(_pdf("teine.pdf"))
    second.locator("form.uxtl__editform").get_by_role(
        "button", name="Lisa fail", exact=True
    ).click()
    wait_for_htmx(page)
    expect(second).to_contain_text("teine.pdf")
    expect(first).not_to_contain_text("teine.pdf")
    expect(page.get_by_text(REFUSAL)).to_have_count(0)
    assert _duplicate_ids(page) == []


def test_a_refused_position_file_stays_on_its_own_row(page, base_url):
    _teema_with_a_step(page, base_url)
    _file_position(page, "Ministeerium toetab muudatust")
    _file_position(page, "Ministeerium vastustab tähtaega")
    first = _pinned(page, _row(page, "Ministeerium toetab muudatust"))
    second = _pinned(page, _row(page, "Ministeerium vastustab tähtaega"))

    _refuse_an_empty_picker_on(page, second)
    _assert_refusal_is_only_on(page, second, [first])

    second.locator("form.uxtl__editform input[type=file]").set_input_files(_pdf("seisukoht.pdf"))
    second.locator("form.uxtl__editform").get_by_role(
        "button", name="Lisa fail", exact=True
    ).click()
    wait_for_htmx(page)
    expect(second).to_contain_text("seisukoht.pdf")
    expect(first).not_to_contain_text("seisukoht.pdf")


def test_a_marge_that_moves_the_stage_moves_the_header_at_once(page, base_url):
    url = _teema_with_a_step(page, base_url)
    open_add_panel(page, "marge-tavaline")
    select = page.locator("#marge-tavaline select[name=stage]")
    chosen = select.locator("option:not([value=''])").nth(1)
    label = (chosen.text_content() or "").strip()
    select.select_option(value=chosen.get_attribute("value"))
    page.locator("#marge-tavaline button[type=submit]").click()
    wait_for_htmx(page)

    header = page.locator("#teema-hetkeseis")
    expect(header.locator(".metaline__value")).to_have_text(label)
    expect(header.locator("select[name=stage] option:checked")).to_contain_text(label)
    assert _duplicate_ids(page) == []

    # And a hard reload says the same thing.
    page.goto(url)
    page.wait_for_load_state("networkidle")
    expect(page.locator("#teema-hetkeseis .metaline__value")).to_have_text(label)


def test_a_marge_with_a_file_moves_the_document_count(page, base_url):
    _teema_with_a_step(page, base_url)
    count = page.locator("#teema-vaated .tabs__count")
    expect(count).to_have_count(0)
    open_add_panel(page, "marge-tavaline")
    page.fill("#id_marge_title", "Ministeerium saatis eelnõu")
    page.locator("#marge-tavaline input[type=file]").set_input_files(_pdf("eelnou.pdf"))
    page.locator("#marge-tavaline button[type=submit]").click()
    wait_for_htmx(page)

    expect(count).to_have_text("· 1")
    assert _duplicate_ids(page) == []


def test_a_refused_publish_keeps_every_id_on_its_own_row(page, base_url):
    url = _teema_with_a_step(page, base_url)
    matter_id = url.rstrip("/").rsplit("/", 1)[-1]
    _plan_through_the_service(matter_id)
    _plan_through_the_service(matter_id)
    page.goto(url)
    page.wait_for_load_state("networkidle")

    rows = page.locator("#kodulehe-ulevaated li.webrow__item")
    expect(rows).to_have_count(2)
    second = rows.nth(1)
    second.locator("details.webrow__publish summary").click()
    second.locator("[name=url]").fill("pole aadress")
    second.get_by_role("button", name="Salvesta avaldatuna").click()
    wait_for_htmx(page)

    second = page.locator("#kodulehe-ulevaated li.webrow__item").nth(1)
    box = second.locator("[name=url]")
    expect(box).to_have_value("pole aadress")
    box_id = box.get_attribute("id")
    assert box_id and box_id != "id_url"
    # The label and the date box are this row's own.
    expect(page.locator(f"label[for='{box_id}'], label:has(#{box_id})")).to_have_count(1)
    date_id = second.locator("[name=published_on]").get_attribute("id")
    assert date_id and date_id != "id_published_on"
    assert _duplicate_ids(page) == []
