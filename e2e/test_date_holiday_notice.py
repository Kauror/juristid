"""A weekend or a public holiday is pointed out under a date box, never refused.

docs/adr/0197. The sentence is the browser's to show, the holidays the
server's to know (`app/core/holidays.py`, `core:public_holidays`), and every
path a day reaches a box by is driven here: typing, the calendar, a quick-date
chip, a header editor an HTMX swap brought in, and a form the server refused.

What is asserted beside each sentence is what makes it informational: no
`aria-invalid`, the ids a refusal already named left where they were, and a
save with a holiday in the box going through.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from app.core.holidays import date_notice, estonian_public_holidays
from app.core.management.commands.seed_e2e_data import OPEN_TITLE
from e2e.conftest import (
    MARTIN,
    SANDRA,
    create_matter,
    open_matter,
    open_next_action_form,
    sign_in,
    start_first_step,
    unique_title,
)

pytestmark = pytest.mark.e2e

JAANIPAEV = "24.06.2027 valitud kuupäev on riigipüha (jaanipäev)."
SATURDAY = "26.06.2027 valitud kuupäev on laupäev."
SUNDAY = "27.06.2027 valitud kuupäev on pühapäev."


def notice(scope, box: str):
    """The sentence under one date box: a sibling of its `.datepicker` wrapper."""
    return scope.locator(f".datepicker:has(> {box}) ~ [data-date-notice]")


def described_by(locator) -> list[str]:
    return (locator.get_attribute("aria-describedby") or "").split()


def next_holiday(after_days: int = 30) -> date:
    """A riigipüha comfortably in the future, so a saved Matter never turns overdue."""
    floor = date.today() + timedelta(days=after_days)
    for year in (floor.year, floor.year + 1):
        for day in estonian_public_holidays(year):
            if day >= floor:
                return day
    raise AssertionError("no holiday within two years")


def written(day: date) -> str:
    return f"{day.day}.{day.month}.{day.year}"


def create_form(page, base_url) -> None:
    page.goto(f"{base_url}/teemad/uus/")
    page.wait_for_load_state("networkidle")


def test_a_typed_holiday_weekend_and_weekday(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)
    box = page.locator("#id_response_deadline")
    hint = notice(page, "#id_response_deadline")

    box.fill("24.6.2027")
    expect(hint).to_have_text(JAANIPAEV)
    expect(hint).to_have_attribute("role", "status")
    hint_id = hint.get_attribute("id")
    assert hint_id in described_by(box)
    assert box.get_attribute("aria-invalid") is None

    box.fill("26.6.2027")
    expect(hint).to_have_text(SATURDAY)
    box.fill("27.06.2027")
    expect(hint).to_have_text(SUNDAY)

    # A weekday, an impossible day and an empty box each take it away again.
    for value in ("25.6.2027", "31.02.2027", ""):
        box.fill("27.6.2027")
        expect(hint).to_have_text(SUNDAY)
        box.fill(value)
        expect(hint).to_have_text("")
        expect(hint).to_be_hidden()
        assert hint_id not in described_by(box)


def test_a_holiday_on_a_weekend_says_only_the_holiday(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)
    # 28.03.2027 is Easter Sunday.
    page.locator("#id_response_deadline").fill("28.3.2027")
    expect(notice(page, "#id_response_deadline")).to_have_text(
        "28.03.2027 valitud kuupäev on riigipüha (ülestõusmispühade 1. püha)."
    )


def test_nothing_is_said_about_a_prefilled_date_on_load(page, base_url):
    """`Saabus` opens on today; even on a Saturday the page says nothing until
    somebody changes it."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)
    expect(page.locator("#id_received_date")).not_to_have_value("")
    expect(page.locator("[data-date-notice]")).to_have_count(0)


def test_a_day_picked_in_the_calendar(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)
    box = page.locator("#id_response_deadline")
    box.fill("1.6.2027")  # a Tuesday: the calendar opens on June 2027
    expect(page.locator("[data-date-notice]")).to_have_count(0)

    picker = box.locator("xpath=..")
    picker.locator(".datepicker__trigger").click()
    panel = picker.locator(".datepicker__panel")
    expect(panel).to_be_visible()
    panel.locator('.datepicker__day[data-datepicker-day="24"]').click()

    expect(box).to_have_value("24.6.2027")
    expect(notice(page, "#id_response_deadline")).to_have_text(JAANIPAEV)


def test_a_holiday_deadline_saves(page, base_url):
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)
    holiday = next_holiday()
    page.fill("#id_title", unique_title("Riigipühal tähtaeg"))
    page.locator("#id_response_deadline").fill(written(holiday))
    expect(notice(page, "#id_response_deadline")).to_have_text(date_notice(holiday) or "")
    page.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))

    expect(page.locator("#teema-pais .metaline")).to_contain_text(written(holiday))


def test_a_refused_form_still_points_out_the_corrected_day(page, base_url):
    """The server's refusal names its message in `aria-describedby`; the
    sentence is added beside it, not instead of it."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)
    page.fill("#id_title", unique_title("Parandatud riigipüha"))
    page.locator("#id_response_deadline").fill("31.02.2027")
    page.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_load_state("networkidle")
    box = page.locator("#id_response_deadline")
    expect(page.locator(".field__error").first).to_contain_text("7.9.2026")
    refusal_ids = described_by(box)

    box.fill("26.6.2027")
    hint = notice(page, "#id_response_deadline")
    expect(hint).to_have_text(SATURDAY)
    assert described_by(box) == [*refusal_ids, hint.get_attribute("id")]


def test_a_header_editor_without_an_id_after_a_refusal(page, base_url):
    """The hand-written `Saabus` editor in the Teema header has no `id`, and its
    refusal is an HTMX swap that brings a new box in (E-04)."""
    sign_in(page, base_url, MARTIN)
    create_form(page, base_url)
    page.fill("#id_title", unique_title("Päise riigipüha"))
    page.get_by_role("button", name="Salvesta", exact=True).click()
    page.wait_for_url(re.compile(r"/teemad/[0-9a-f-]{36}/$"))
    start_first_step(page)

    form = page.locator("#teema-pais form[hx-post$='/vali/received_date/']")
    form.locator("xpath=ancestor::details[1]/summary").click()
    box = form.locator("input[name=received_date]")
    assert box.get_attribute("id") is None
    box.fill("31.02.2026")
    box.press("Enter")
    page.wait_for_load_state("networkidle")
    expect(form.locator(".field__error")).to_contain_text("7.9.2026")

    box.fill("20.8.2026")
    hint = notice(form, "input[name=received_date]")
    expect(hint).to_have_text("20.08.2026 valitud kuupäev on riigipüha (taasiseseisvumispäev).")
    hint_id = hint.get_attribute("id")
    assert hint_id
    assert page.locator(f"[id='{hint_id}']").count() == 1
    assert "paise-saabus-error" in described_by(box)
    assert hint_id in described_by(box)


def test_a_quick_date_chip_is_read_like_a_typed_day(page, base_url):
    """The chips fire only `change`; each day they write is judged by the same
    rules as the server's (whatever today happens to be)."""
    sign_in(page, base_url, SANDRA)
    open_matter(page, base_url, OPEN_TITLE)
    open_next_action_form(page)
    hint = notice(page, "#id_target_date")

    for label in ("Täna", "Homme", "+1 nädal", "+2 nädalat"):
        chip = page.locator("#lisa-jargmine [data-quickdate]").filter(has_text=label).first
        chip.click()
        value = page.locator("#id_target_date").input_value()
        day, month, year = (int(part) for part in value.split("."))
        expected = date_notice(date(year, month, day))
        if expected:
            expect(hint).to_have_text(expected)
        else:
            expect(page.locator("#lisa-jargmine [data-date-notice]:not(:empty)")).to_have_count(0)


def test_a_precision_other_than_a_day_says_nothing(page, base_url):
    sign_in(page, base_url, SANDRA)
    open_matter(page, base_url, OPEN_TITLE)
    open_next_action_form(page)
    box = page.locator("#id_target_date")
    hint = notice(page, "#id_target_date")

    box.fill("26.6.2027")
    expect(hint).to_have_text(SATURDAY)

    page.locator("#lisa-jargmine label.precision__chip", has_text="Kvartal").first.click()
    expect(hint).to_have_text("")
    assert hint.get_attribute("id") not in described_by(box)

    page.locator("#lisa-jargmine label.precision__chip", has_text="Täpne päev").first.click()
    expect(hint).to_have_text(SATURDAY)


def test_a_fact_form_period_other_than_a_day_says_nothing(page, base_url):
    """The `periodfields` control (intelligence/partials/fact_fields.html):
    a hidden day box describes nothing, and its sentence goes with it."""
    sign_in(page, base_url, MARTIN)
    url = create_matter(page, base_url, unique_title("Oluline tähtaeg riigipühal"))
    page.goto(url.rstrip("/") + "/olulised-tahtajad/lisa/")
    page.wait_for_load_state("networkidle")
    form = page.locator("form.factform")
    day = form.get_by_label("Kuupäev", exact=True)
    hint = notice(form, "#" + (day.get_attribute("id") or ""))

    day.fill("24.12.2027")
    expect(hint).to_have_text("24.12.2027 valitud kuupäev on riigipüha (jõululaupäev).")

    form.get_by_label("Kvartali täpsusega").check()
    expect(day).to_be_hidden()
    expect(form.locator("[data-date-notice]:not(:empty)")).to_have_count(0)
    assert hint.get_attribute("id") not in described_by(day)

    form.get_by_label("Täpne kuupäev").check()
    expect(hint).to_have_text("24.12.2027 valitud kuupäev on riigipüha (jõululaupäev).")


def test_a_year_is_asked_for_once(page, base_url):
    sign_in(page, base_url, MARTIN)
    asked: list[str] = []
    page.on(
        "request",
        lambda request: (
            asked.append(request.url) if "/kuupaevad/riigipuhad/" in request.url else None
        ),
    )
    create_form(page, base_url)
    box = page.locator("#id_response_deadline")
    hint = notice(page, "#id_response_deadline")

    box.fill("24.6.2027")
    expect(hint).to_have_text(JAANIPAEV)
    box.fill("26.6.2027")
    expect(hint).to_have_text(SATURDAY)
    box.fill("24.6.2027")
    expect(hint).to_have_text(JAANIPAEV)

    assert asked == [f"{base_url}/kuupaevad/riigipuhad/2027/"]


def test_without_the_holidays_the_weekends_still_speak(page, base_url, no_feature_fails_to_start):
    """A failed request is never an error on the page: weekends only."""
    sign_in(page, base_url, MARTIN)
    page.route("**/kuupaevad/riigipuhad/**", lambda route: route.abort())
    create_form(page, base_url)
    box = page.locator("#id_response_deadline")
    hint = notice(page, "#id_response_deadline")

    box.fill("26.6.2027")
    expect(hint).to_have_text(SATURDAY)
    # Thursday jaanipäev, unknown without the list: nothing, rather than a guess.
    box.fill("24.6.2027")
    expect(hint).to_have_text("")
    box.fill("27.6.2027")
    expect(hint).to_have_text(SUNDAY)
    assert no_feature_fails_to_start == []
