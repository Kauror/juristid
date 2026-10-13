"""Uus teema's dates and its chosen related Teemad, in a real browser (owner, 2026-10-13).

* `Saabus` left of `Arvamuse tähtaeg` on one row from 1440 to 1024 px, above
  it on a phone (docs/adr/0130, amendment of 2026-10-13).
* Switching `Õigusakt` to and from «Koja ettepanek või pöördumine», in both
  directions and repeatedly, hides and restores `Saabus` and never touches the
  deadline (docs/adr/0151, amendment of 2026-10-13).
* `Seo olemasoleva teemaga`: the chosen list is under the heading and above the
  search; choosing, the keyboard, changing the query, taking one back, choosing
  it again and saving all behave, and a refused save keeps every choice.

The server half is tests/test_create_dates_and_related_matters.py.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, sign_in, unique_title
from e2e.test_matter_form_ux import _file, create_form, typed_date

pytestmark = pytest.mark.e2e

INITIATIVE = 'input[name="legal_instruments"][data-instrument-key="koja-ettepanek"]'
ORDINARY = 'input[name="legal_instruments"][data-instrument-key="seadus"]'
CHOSEN = "[data-related-chosen] li"


def _box(page, selector: str) -> dict:
    found = page.locator(selector).first.bounding_box()
    assert found is not None, selector
    return found


def _overflow(page) -> float:
    return page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [1440, 1024, 375])
def test_saabus_comes_first_at_every_width(page, base_url, width):
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 1000})
    create_form(page, base_url)

    received = _box(page, "#id_received_date")
    deadline = _box(page, "#id_response_deadline")
    if width >= 1024:
        assert received["x"] < deadline["x"], (received, deadline)
        assert abs(received["y"] - deadline["y"]) < 4, (received, deadline)
    else:
        assert received["y"] < deadline["y"], (received, deadline)
    assert page.locator("#id_received_date").input_value()
    expect(page.locator("#id_response_deadline")).to_have_value("")
    assert _overflow(page) <= 1


def test_switching_the_instrument_never_loses_the_deadline(page, base_url):
    """Ordinary → initiative → ordinary, then initiative → ordinary → initiative."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)
    received = page.locator("#id_received_date")
    deadline = page.locator("#id_response_deadline")
    held = received.input_value()
    due = typed_date(15)
    deadline.fill(due)

    for _ in range(2):
        page.locator(INITIATIVE).check()
        expect(received).to_be_hidden()
        expect(deadline).to_be_visible()
        expect(deadline).to_have_value(due)
        assert received.input_value() == ""

        page.locator(ORDINARY).check()
        expect(received).to_be_visible()
        assert received.input_value() == held
        expect(deadline).to_have_value(due)

    page.locator(INITIATIVE).check()
    expect(received).to_be_hidden()
    expect(deadline).to_have_value(due)
    # One box each, whatever was switched.
    expect(page.locator('input[name="received_date"]')).to_have_count(1)
    expect(page.locator('input[name="response_deadline"]')).to_have_count(1)

    page.fill("#id_title", unique_title("Vahetatud algatus"))
    page.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    meta = page.locator(".metaline")
    expect(meta).to_contain_text("Koja ettepanek või pöördumine")
    expect(meta).to_contain_text(due)
    expect(meta).not_to_contain_text("Saabus")


# ---------------------------------------------------------------------------
# Seo olemasoleva teemaga
# ---------------------------------------------------------------------------


def _search(page, term: str) -> None:
    page.locator("#seotud-teema-otsing").fill(term)
    page.wait_for_selector("#seotud-teema-tulemused [data-related-pick], #seotud-teema-tulemused p")


def _pick(page, title: str) -> None:
    _search(page, title)
    page.locator("#seotud-teema-tulemused").get_by_role(
        "button", name=f"Vali seotud teemaks: {title}"
    ).click()


@pytest.fixture
def three_earlier(page, base_url) -> list[str]:
    sign_in(page, base_url, MARTIN)
    marker = unique_title("Seoseproov").split()[-1]
    titles = [
        f"Seoseproov {marker} esimene",
        f"Seoseproov {marker} teine — "
        + "väga pika pealkirjaga teema, mis peab murduma mitmele reale " * 2,
        f"Seoseproov {marker} kolmas",
    ]
    for title in titles:
        _file(page, base_url, title)
    return titles


def test_the_chosen_list_is_above_the_search_and_survives_everything(page, base_url, three_earlier):
    first, second, third = three_earlier
    create_form(page, base_url)
    chosen = page.locator(CHOSEN)
    box = page.locator("#seotud-teema-otsing")
    # Nothing chosen takes no space between the heading and the box.
    gap_before = (
        _box(page, "#seotud-teema-otsing")["y"] - _box(page, "label[for=seotud-teema-otsing]")["y"]
    )
    expect(page.locator("[data-related-chosen]")).to_be_hidden()

    for title in (first, second, third):
        _pick(page, title)
        expect(chosen.last).to_contain_text(title.split(" — ")[0])
        expect(box).to_be_focused()
    expect(chosen).to_have_count(3)

    # Between the heading and the search, and the results below the search.
    heading_y = _box(page, "label[for=seotud-teema-otsing]")["y"]
    list_box = _box(page, "[data-related-chosen]")
    box_y = _box(page, "#seotud-teema-otsing")["y"]
    assert heading_y < list_box["y"] < box_y
    assert list_box["y"] + list_box["height"] <= box_y + 1
    assert gap_before < box_y - heading_y

    # A chosen Teema is not offered again, so it cannot be listed twice.
    _search(page, first)
    expect(
        page.locator("#seotud-teema-tulemused").get_by_role(
            "button", name=f"Vali seotud teemaks: {first}"
        )
    ).to_have_count(0)
    expect(chosen).to_have_count(3)
    # Changing and clearing the query leaves the list alone.
    box.fill("")
    expect(chosen).to_have_count(3)

    # × is a real button named after its Teema; removing one leaves the others.
    remove = page.get_by_role("button", name=f"Eemalda valik: {second}")
    expect(remove).to_be_visible()
    remove.click()
    expect(chosen).to_have_count(2)
    expect(page.locator("[data-related-chosen]")).not_to_contain_text("väga pika pealkirjaga")
    expect(box).to_be_focused()

    # Taken back, it may be chosen again — then taken back for good.
    _pick(page, second)
    expect(chosen).to_have_count(3)
    page.get_by_role("button", name=f"Eemalda valik: {second}").click()
    expect(chosen).to_have_count(2)

    page.fill("#id_title", unique_title("Kahe seosega uus teema"))
    page.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    related = page.locator("#seotud-materjalid")
    expect(related).to_contain_text(first)
    expect(related).to_contain_text(third)
    expect(related).not_to_contain_text("väga pika pealkirjaga")


def test_the_keyboard_chooses_and_enter_never_files_the_teema(page, base_url, three_earlier):
    first = three_earlier[0]
    create_form(page, base_url)
    box = page.locator("#seotud-teema-otsing")
    box.focus()
    page.keyboard.type(first, delay=5)
    page.keyboard.press("Enter")
    assert page.url.endswith("/teemad/uus/")
    pick = page.locator("#seotud-teema-tulemused").get_by_role(
        "button", name=f"Vali seotud teemaks: {first}"
    )
    expect(pick).to_be_visible()
    pick.focus()
    page.keyboard.press("Enter")
    expect(page.locator(CHOSEN)).to_have_count(1)
    expect(box).to_be_focused()

    remove = page.get_by_role("button", name=f"Eemalda valik: {first}")
    remove.focus()
    page.keyboard.press("Enter")
    expect(page.locator(CHOSEN)).to_have_count(0)
    expect(box).to_be_focused()


def test_a_refused_save_keeps_every_choice_in_place(page, base_url, three_earlier):
    first, second, _third = three_earlier
    create_form(page, base_url)
    _pick(page, first)
    _pick(page, second)
    page.locator("form.createform").evaluate("form => form.noValidate = true")
    page.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")

    expect(page.locator(".field__error").first).to_be_visible()
    chosen = page.locator(CHOSEN)
    expect(chosen).to_have_count(2)
    expect(chosen.nth(0)).to_contain_text(first)
    expect(chosen.nth(1)).to_contain_text("väga pika pealkirjaga")
    assert re.fullmatch(
        r"\d{4}_\d+", (chosen.nth(0).locator(".relpick__ref").text_content() or "").strip()
    )
    assert _box(page, "[data-related-chosen]")["y"] < _box(page, "#seotud-teema-otsing")["y"]


def test_a_long_chosen_title_keeps_its_remove_button_in_view_on_a_phone(
    page, base_url, three_earlier
):
    second = three_earlier[1]
    page.set_viewport_size({"width": 375, "height": 900})
    create_form(page, base_url)
    _pick(page, second)

    remove = page.get_by_role("button", name=f"Eemalda valik: {second}")
    expect(remove).to_be_visible()
    found = remove.bounding_box()
    assert found is not None and found["x"] + found["width"] <= 375
    title = _box(page, f"{CHOSEN} .relpick__title")
    assert title["height"] > 30, "a long title does not wrap"
    assert _overflow(page) <= 1
