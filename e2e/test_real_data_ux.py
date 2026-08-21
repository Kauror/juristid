"""The Stage-2E.1 corrections, in a browser.

Everything here is proved elsewhere against the database. What only a browser
shows is whether the corrections *land*: whether a year is one click away,
whether a filename does the thing it looks like it does, and whether the new
creation form is fillable without opening a single dropdown.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from e2e.conftest import MARTIN, sign_in

pytestmark = pytest.mark.e2e


# -- statistics: every year directly selectable -----------------------------


def test_a_single_year_is_one_click_away(page, base_url, screenshots):
    """Not a chart bar somebody has to find and click to discover the URL."""
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/statistika/")

    picker = page.locator(".yearpicker")
    expect(picker).to_be_visible()

    years = picker.locator(".yearpicker__year")
    expect(years.first).to_be_visible()
    labels = years.all_inner_texts()
    assert labels == sorted(labels, reverse=True), "newest year first"

    screenshots(page, "statistika-aastad")

    years.first.click()
    page.wait_for_url(re.compile(r"periood=\d{4}"))
    expect(page.locator(".yearpicker__year.is-active")).to_have_count(1)


def offered_years(page, base_url) -> list[str]:
    """Whatever years this world actually holds.

    Hard-coding 2024 made these tests assert something about the *fixture*
    rather than about the picker — and the seeded world has neither 2024 nor a
    guarantee it ever will.
    """
    page.goto(f"{base_url}/statistika/")
    return page.locator(".yearpicker__year").all_inner_texts()


def test_a_year_survives_the_back_button(page, base_url):
    """A statistics URL is something people forward to each other."""
    sign_in(page, base_url, MARTIN)
    years = offered_years(page, base_url)
    if len(years) < 2:
        pytest.skip("this world spans fewer than two register years")

    first, second = years[0], years[1]
    page.goto(f"{base_url}/statistika/?periood={first}")
    expect(page.locator(".yearpicker__year.is-active")).to_have_text(first)

    page.goto(f"{base_url}/statistika/?periood={second}")
    expect(page.locator(".yearpicker__year.is-active")).to_have_text(second)

    page.go_back()
    expect(page.locator(".yearpicker__year.is-active")).to_have_text(first)


def test_the_year_filter_holds_across_the_tabs(page, base_url):
    sign_in(page, base_url, MARTIN)
    years = offered_years(page, base_url)
    if not years:
        pytest.skip("this world has no register years")

    year = years[0]
    for path in ("teemad", "tegevus", "ajalooline", "andmekvaliteet"):
        page.goto(f"{base_url}/statistika/{path}/?periood={year}")
        expect(page.locator(".yearpicker__year.is-active")).to_have_text(year)


# -- Uus teema ---------------------------------------------------------------


def open_create(page, base_url):
    page.goto(f"{base_url}/teemad/uus/")
    expect(page.get_by_role("heading", name="Uus teema")).to_be_visible()


def test_the_form_is_fillable_without_opening_a_dropdown(page, base_url, screenshots):
    """The whole point of the redesign, as one assertion.

    Title, files, owner, date, sender and areas are all visible at once. For a
    department of four, a select is a click spent finding out what the options
    are (Stage-2E.1 brief 14, 15).
    """
    sign_in(page, base_url, MARTIN)
    open_create(page, base_url)

    expect(page.locator("#id_title")).to_be_visible()
    expect(page.locator(".dropzone")).to_be_visible()
    expect(page.locator(".choicecards").first).to_be_visible()
    expect(page.locator(".checkitems").first).to_be_visible()
    expect(page.locator("#id_received_date")).to_be_visible()
    screenshots(page, "uus-teema")


def test_the_arrival_date_starts_today_and_stays_editable(page, base_url):
    import datetime

    sign_in(page, base_url, MARTIN)
    open_create(page, base_url)

    field = page.locator("#id_received_date")
    today = datetime.date.today().isoformat()
    expect(field).to_have_value(today)

    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    field.fill(yesterday)
    expect(field).to_have_value(yesterday)


def test_only_one_owner_can_be_chosen(page, base_url):
    """Radios, not checkboxes: `Matter.owner` is one person."""
    sign_in(page, base_url, MARTIN)
    open_create(page, base_url)

    owners = page.locator("input[name='owner']")
    expect(owners.first).to_have_attribute("type", "radio")

    if owners.count() >= 2:
        owners.nth(0).check()
        owners.nth(1).check()
        expect(owners.nth(0)).not_to_be_checked()
        expect(owners.nth(1)).to_be_checked()


def test_only_one_sender_can_be_chosen(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_create(page, base_url)
    senders = page.locator("input[name='source_organisation']")
    if senders.count():
        expect(senders.first).to_have_attribute("type", "radio")


def test_the_long_tail_of_senders_is_searchable(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_create(page, base_url)

    page.get_by_text("Muu / otsi organisatsiooni").click()
    expect(page.locator("#id_source_organisation_other")).to_be_visible()


def test_several_policy_areas_can_be_ticked(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_create(page, base_url)

    areas = page.locator("input[name='policy_areas']")
    expect(areas.first).to_have_attribute("type", "checkbox")
    if areas.count() >= 2:
        areas.nth(0).check()
        areas.nth(1).check()
        expect(areas.nth(0)).to_be_checked()
        expect(areas.nth(1)).to_be_checked()


def test_muu_reveals_its_own_text_field(page, base_url):
    sign_in(page, base_url, MARTIN)
    open_create(page, base_url)

    text = page.locator("#valdkond-muu-tekst")
    expect(text).to_be_hidden()

    page.locator("#id_policy_area_other_selected").check()
    expect(text).to_be_visible()


def test_the_visibility_control_is_absent(page, base_url):
    """The model, the enum and every restricted record are untouched.

    What is gone is the control: restricting a Matter is a rare, deliberate act,
    and on the creation screen it was a field to skim past (brief 21).
    """
    sign_in(page, base_url, MARTIN)
    open_create(page, base_url)

    expect(page.locator("[name='visibility']")).to_have_count(0)
    expect(page.get_by_text("Nähtavus")).to_have_count(0)


def test_a_matter_can_be_created_with_a_file_attached(page, base_url, tmp_path):
    sign_in(page, base_url, MARTIN)
    open_create(page, base_url)

    attachment = tmp_path / "kaaskiri.txt"
    attachment.write_text("Näidiskaaskiri browseri testist.", encoding="utf-8")

    page.locator("#id_title").fill("Browseri testist loodud teema")
    page.locator("#id_files").set_input_files(str(attachment))
    expect(page.locator(".dropzone__file")).to_have_text("kaaskiri.txt")

    page.get_by_role("button", name="Loo teema").click()
    page.wait_for_load_state("domcontentloaded")

    # If the form refused, say what it said. A bare navigation timeout tells you
    # only that nothing happened, which is the least useful half of the answer.
    complaints = page.locator(".field__error, .formerror, .message--error").all_inner_texts()
    assert not complaints, f"the form refused: {complaints}"

    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    expect(page.get_by_role("heading", name="Browseri testist loodud teema")).to_be_visible()


# -- historical files --------------------------------------------------------


def test_a_pdf_filename_opens_the_file_rather_than_a_page_about_it(page, base_url):
    """The click should reach the material, not an intermediate screen."""
    sign_in(page, base_url, MARTIN)
    page.goto(f"{base_url}/teemad/")
    page.get_by_role("link", name=re.compile("Tavaline avatud teema")).first.click()

    historical = page.get_by_role("link", name="Vaata ajaloolist materjali")
    if not historical.count():
        pytest.skip("this world has no historical material attached")

    historical.click()
    link = page.locator(".casefile__name").first
    expect(link).to_be_visible()

    href = link.get_attribute("href") or ""
    assert "/ava/" in href or href.endswith("/"), "the filename links straight at the bytes"
