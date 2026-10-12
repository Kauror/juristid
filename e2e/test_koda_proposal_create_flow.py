"""The owner's round of 2026-10-12, in a real browser.

docs/adr/0151 and 0130 amendments, docs/adr/0155, docs/adr/0156. What only a
browser can show:

* «Märgi saadetud Koja arvamuseks» appears only for `Õigusakt` «Koja ettepanek
  või pöördumine» *with a file*, and goes — unticked — when either stops being
  true; ticked, `Saadetud` (today) and `Adressaat` open under it;
* a sent proposal filed that way lands on a Teema whose current work is the
  thirty-day check;
* the action bar is the form's last element, `Loobu` then `Salvesta`,
  right-aligned at desktop width and inside the window on a phone;
* the stage prefill chooses `Idee` and says nothing;
* the header's `Hetkeseis` is a menu that saves on one choice, by mouse and by
  keyboard, and `Teemaviide` reads under `Teema andmed`.
"""

from __future__ import annotations

import re
from datetime import date

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, choose_organisation, sign_in, unique_title

PROPOSAL = 'input[name="legal_instruments"][data-instrument-key="koja-ettepanek"]'
ORDINARY = 'input[name="legal_instruments"][data-instrument-key="seadus"]'
SENT_BOX = "#saadetud-koja-arvamus"
SENT_TOGGLE = 'input[name="koda_sent"]'
FOLLOW_UP = "Kontrolli, mis on pöördumisest saanud"


def _pdf(name: str) -> dict:
    return {
        "name": name,
        "mimeType": "application/pdf",
        "buffer": b"%PDF-1.4\n% synthetic e2e " + name.encode() + b"\n%%EOF\n",
    }


def _today() -> str:
    today = date.today()
    return f"{today.day}.{today.month}.{today.year}"


def _open(page, base_url) -> None:
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")


def _add_file(page, name: str = "Pöördumine.pdf") -> None:
    page.locator("#id_files").set_input_files([_pdf(name)])
    expect(page.locator("[data-upload-zone] .dropzone__file")).not_to_have_count(0)


def test_the_sent_box_appears_only_for_the_proposal_with_a_file(page, base_url):
    sign_in(page, base_url, MARTIN)
    _open(page, base_url)
    section = page.locator(SENT_BOX)
    expect(section).to_be_hidden()

    # The instrument alone is not enough …
    page.locator(PROPOSAL).check()
    expect(section).to_be_hidden()
    # … and a file under another instrument is not either.
    page.locator(ORDINARY).check()
    _add_file(page)
    expect(section).to_be_hidden()

    page.locator(PROPOSAL).check()
    expect(section).to_be_visible()
    expect(section).to_contain_text("Märgi saadetud Koja arvamuseks")
    expect(section.locator("[data-sent-proposal-fields]")).to_be_hidden()

    page.locator(SENT_TOGGLE).check()
    fields = section.locator("[data-sent-proposal-fields]")
    expect(fields).to_be_visible()
    expect(page.locator("#id_sent_on")).to_have_value(_today())
    expect(fields).to_contain_text("Adressaat")

    # Another instrument takes it away and clears what was ticked.
    page.locator(ORDINARY).check()
    expect(section).to_be_hidden()
    expect(page.locator(SENT_TOGGLE)).not_to_be_checked()
    page.locator(PROPOSAL).check()
    expect(section).to_be_visible()
    expect(page.locator(SENT_TOGGLE)).not_to_be_checked()


def test_removing_the_last_file_takes_the_sent_box_away(page, base_url):
    sign_in(page, base_url, MARTIN)
    _open(page, base_url)
    page.locator(PROPOSAL).check()
    _add_file(page)
    page.locator(SENT_TOGGLE).check()

    page.locator(
        "[data-upload-zone] .dropzone__file"
        " :is(button[data-intake-remove], button.uploadqueue__remove, button[data-drop-held])"
    ).first.click()
    expect(page.locator("[data-upload-zone] .dropzone__file")).to_have_count(0)

    expect(page.locator(SENT_BOX)).to_be_hidden()
    expect(page.locator(SENT_TOGGLE)).not_to_be_checked()


def test_an_already_sent_proposal_lands_on_its_check(page, base_url):
    sign_in(page, base_url, MARTIN)
    _open(page, base_url)
    title = unique_title("Saadetud pöördumine brauserist")
    page.fill("#id_title", title)
    page.locator(PROPOSAL).check()
    # The prefill chooses `Idee` and says nothing about it.
    expect(page.locator('input[name="stage"][data-stage-key="idea"]')).to_be_checked()
    expect(page.locator("[data-stage-prefill-note]")).to_have_count(0)
    _add_file(page)
    page.locator(SENT_TOGGLE).check()
    choose_organisation(page, "saadetud-adressaat")

    page.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))

    current = page.locator("#praegune-tegevus, .curact").first
    expect(current).to_contain_text(FOLLOW_UP)
    expect(page.locator("body")).not_to_contain_text("Järgmine samm on määramata")
    expect(page.locator(".metaline")).to_contain_text("Koja ettepanek või pöördumine")


@pytest.mark.parametrize("width", [1440, 1024, 375])
def test_the_action_bar_ends_the_form(page, base_url, width):
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 900})
    _open(page, base_url)

    form = page.locator("form.createform")
    actions = form.locator(".createform__actions")
    related = page.locator("#seo-olemasoleva-teemaga")
    cancel = actions.get_by_role("link", name="Loobu")
    save = actions.get_by_role("button", name="Salvesta", exact=True)
    expect(related).to_be_visible()
    actions_box, related_box = actions.bounding_box(), related.bounding_box()
    cancel_box, save_box, form_box = cancel.bounding_box(), save.bounding_box(), form.bounding_box()
    assert actions_box and related_box and cancel_box and save_box and form_box
    assert actions_box["y"] >= related_box["y"] + related_box["height"] - 1
    assert save_box["x"] > cancel_box["x"]
    # Right-aligned: the primary action ends where the form ends.
    assert abs((save_box["x"] + save_box["width"]) - (actions_box["x"] + actions_box["width"])) <= 2
    # The last element of the form.
    assert page.evaluate(
        "() => document.querySelector('form.createform').lastElementChild"
        ".classList.contains('createform__actions')"
    )
    assert page.evaluate("() => document.documentElement.scrollWidth") <= width


def _new_matter(page, base_url) -> str:
    _open(page, base_url)
    page.fill("#id_title", unique_title("Päise hetkeseis"))
    page.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    return page.url


def test_the_header_stage_is_chosen_with_one_click(page, base_url):
    sign_in(page, base_url, MARTIN)
    _new_matter(page, base_url)
    slot = page.locator("#teema-hetkeseis")
    expect(slot).to_contain_text("Vali")

    slot.locator("summary").click()
    menu = slot.locator(".stagepick__menu")
    expect(menu).to_be_visible()
    menu.get_by_role("button", name="Kooskõlastusringil", exact=True).click()

    expect(page.locator("#teema-hetkeseis summary")).to_contain_text("Kooskõlastusringil")
    expect(page.locator("#teema-hetkeseis .stagepick__menu")).to_be_hidden()


def test_the_header_stage_works_from_the_keyboard(page, base_url):
    sign_in(page, base_url, MARTIN)
    _new_matter(page, base_url)
    summary = page.locator("#teema-hetkeseis summary")

    summary.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#teema-hetkeseis .stagepick__menu")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#teema-hetkeseis .stagepick__menu")).to_be_hidden()

    summary.focus()
    page.keyboard.press("Enter")
    page.locator("#teema-hetkeseis").get_by_role("button", name="Idee", exact=True).focus()
    page.keyboard.press("Enter")
    expect(page.locator("#teema-hetkeseis summary")).to_contain_text("Idee")


def test_teemaviide_reads_under_teema_andmed(page, base_url):
    sign_in(page, base_url, MARTIN)
    _new_matter(page, base_url)

    expect(page.locator(".metaline")).not_to_contain_text("Teemaviide")
    expect(page.get_by_role("button", name="Kopeeri teemaviide")).to_have_count(0)
    rail = page.locator("#teema-andmed")
    expect(rail).to_contain_text("Teemaviide")
    expect(rail.locator("#teemaviide")).to_have_text(re.compile(r"^\d{4}_\d+$"))


@pytest.mark.parametrize("width", [1440, 375])
def test_the_header_stage_menu_opens_on_screen(page, base_url, width):
    """The defect behind «Hetkeseis is not editable»: the header's editors were
    anchored to nothing and opened one viewport height down the page. The menu
    must open beside its value and inside the window (docs/adr/0156)."""
    sign_in(page, base_url, MARTIN)
    page.set_viewport_size({"width": width, "height": 800})
    _new_matter(page, base_url)
    trigger = page.locator("#teema-hetkeseis summary")
    trigger.click()
    menu = page.locator("#teema-hetkeseis .stagepick__menu")
    expect(menu).to_be_visible()

    box, anchor = menu.bounding_box(), trigger.bounding_box()
    assert box and anchor
    assert box["x"] >= 0 and box["x"] + box["width"] <= width, box
    assert box["y"] >= 0 and box["y"] + box["height"] <= 800, box
    assert abs(box["y"] - (anchor["y"] + anchor["height"])) < 40, (box, anchor)
    assert page.evaluate("() => document.documentElement.scrollWidth") <= width
