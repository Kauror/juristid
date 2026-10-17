"""`Tagasisidet ootame kuni`'s calendar shows the Matter's `Arvamuse tähtaeg`, in a browser.

`+ Kaasamine · Alusta kaasamist`: opening the feedback-deadline calendar marks the
opinion deadline in the header's deadline orange, says «Arvamuse tähtaeg» on
hover and to a screen reader, and names it under the grid so it can be found in
another month. The mark is information only: nothing is pre-selected, any day —
the marked one or a later one — is chosen the ordinary way, and a Matter without
an opinion deadline shows the calendar unchanged.

The suite reads state off the page only (e2e/conftest.py).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from e2e.conftest import SANDRA, create_matter, open_add_panel, sign_in, wait_for_htmx
from e2e.test_process_strip import create_matter_with_deadline
from e2e.titles import unique_title

pytestmark = pytest.mark.e2e

LABEL = "Arvamuse tähtaeg"


def _et(on: date) -> str:
    return f"{on.day}.{on.month}.{on.year}"


def _open_alusta_kaasamist(page):
    # Both chips are toggles; the shared helper opens the family, then the child.
    open_add_panel(page, "kaasamine-alusta")
    form = page.locator("#kaasamine-alusta")
    expect(form).to_be_visible()
    return form


def _feedback_picker(form):
    return form.locator('.datepicker:has(input[name="feedback_deadline"])')


def _open_calendar(form):
    picker = _feedback_picker(form)
    picker.locator(".datepicker__trigger").click()
    panel = picker.locator(".datepicker__panel")
    expect(panel).to_be_visible()
    return picker, panel


def _show_month_of(panel, target: date) -> None:
    """`Järgmine kuu` until the panel shows ``target``'s month (at most a year)."""
    for _ in range(12):
        if panel.locator(".datepicker__day.is-marked").count():
            return
        panel.get_by_role("button", name="Järgmine kuu").click()
    raise AssertionError(f"no marked day within a year of today for {target}")


def test_the_feedback_calendar_marks_the_opinion_deadline(page, base_url):
    deadline = date.today() + timedelta(days=45)  # always a later month than today's
    sign_in(page, base_url, SANDRA)
    create_matter_with_deadline(
        page, base_url, unique_title("Kalendri tähtaeg"), deadline=_et(deadline)
    )
    header_orange = page.evaluate(
        "() => getComputedStyle(document.querySelector('.metaline__value--deadline')).color"
    )

    form = _open_alusta_kaasamist(page)
    feedback = form.locator('input[name="feedback_deadline"]')
    expect(feedback).to_have_value("")
    picker, panel = _open_calendar(form)

    # Named in words under the grid while today's month is shown.
    expect(panel.locator(".datepicker__legend")).to_have_text(f"{LABEL} {_et(deadline)}")
    expect(panel.locator(".datepicker__day.is-marked")).to_have_count(0)

    # Month navigation reaches it; one marked day, in the header's orange.
    _show_month_of(panel, deadline)
    marked = panel.locator(".datepicker__day.is-marked")
    expect(marked).to_have_count(1)
    expect(marked).to_have_text(str(deadline.day))
    expect(marked).to_have_attribute("title", LABEL)
    assert marked.evaluate("el => getComputedStyle(el).color") == header_orange

    # The accessible name says what the day is; nothing was chosen by opening.
    by_name = panel.get_by_role("button", name=f"{_et(deadline)}, {LABEL}", exact=True)
    expect(by_name).to_have_count(1)
    expect(feedback).to_have_value("")

    # Keyboard: the marked day is an ordinary button — focus and Enter choose it.
    by_name.focus()
    expect(by_name).to_be_focused()
    page.keyboard.press("Enter")
    expect(feedback).to_have_value(_et(deadline))
    expect(panel).to_be_hidden()

    # A later day is chosen the usual way; the deadline does not limit it.
    later = deadline + timedelta(days=3)
    picker.locator(".datepicker__trigger").click()
    _show_month_of(panel, deadline)
    if later.month != deadline.month:
        panel.get_by_role("button", name="Järgmine kuu").click()
    panel.get_by_role("button", name=_et(later), exact=True).click()
    expect(feedback).to_have_value(_et(later))

    form.locator('[name="audience"]').fill("Liikmed (sünteetiline)")
    with page.expect_response(
        lambda r: r.url.endswith("/lisa/kaasamine/") and r.request.method == "POST"
    ) as answer:
        form.get_by_role("button", name="Salvesta", exact=True).click()
    wait_for_htmx(page)
    assert answer.value.status == 200


def test_without_an_opinion_deadline_the_calendar_is_unchanged(page, base_url):
    sign_in(page, base_url, SANDRA)
    create_matter(page, base_url, unique_title("Kalender ilma tähtajata"))

    form = _open_alusta_kaasamist(page)
    _, panel = _open_calendar(form)

    expect(panel.locator(".datepicker__legend")).to_have_count(0)
    expect(panel.locator(".datepicker__day.is-marked")).to_have_count(0)
    panel.get_by_role("button", name="Järgmine kuu").click()
    expect(panel.locator(".datepicker__day.is-marked")).to_have_count(0)
    expect(panel.locator(".datepicker__legend")).to_have_count(0)
