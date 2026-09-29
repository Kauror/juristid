"""The compact date cell on a work row, when the source named a period.

*september 2026* is the honest way to write a month, and since docs/adr/0120
(UQ-09) it is what this column prints. It used to print ``09.26`` — the month,
then the year's last two digits — to fit a narrow column, and a lawyer read
«10.26» under a column of days as a day and a month. So a month is written in
words here exactly as `format_at_precision` writes it on every other surface,
beside the quarter, half-year and year that always were.

The rule this file still exists to hold is that the cell never turns a period
into a day: not ``01.09.2026``, not ``01.09``, and not ``täna`` printed on the
first of the month — each is the failure master specification §3.5 is about,
and each is asserted against below.

Fixed dates rather than offsets from today, because the assertion is literally
about which characters are printed for a named month; ``today`` is passed in, so
nothing here depends on when it is run. The two tests that go through a view use
offsets instead, since a view reads the real clock.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from app.intelligence.services import add_important_date
from app.matters import work_items as wi
from app.matters.my_work import build_my_work
from app.matters.services import create_matter
from app.workflow.dates import format_at_precision, period_bounds
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics
from app.workflow.services import set_next_action

pytestmark = pytest.mark.django_db

#: A Wednesday well before every fixture period below, so nothing under test is
#: late and the cell prints the period rather than a count of days past it.
TODAY = date(2026, 8, 12)

#: The end of TODAY's ISO week, and the horizon the page would build from it.
WEEK_END = date(2026, 8, 16)

MY_WORK = reverse("matters:my_work")

#: Two numbers and nothing else — the retired month spelling, and a day's.
TWO_NUMBERS = re.compile(r"^\d{2}\.\d{2}$")


def _matter(owner, title="Näidisteema"):
    return create_matter(title=title, owner=owner, reference_year=2026)


def _action(owner, *, on, precision, title, text="Esitan arvamuse"):
    matter = _matter(owner, title=title)
    set_next_action(
        matter=matter,
        text=text,
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=on,
        date_precision=precision,
        actor=owner,
    )
    return matter


def _items(owner, today=TODAY):
    return {item.matter.title: item for item in wi.work_items(owner, today=today)}


# ---------------------------------------------------------------------------
# A month is written in words
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("anchor", "expected"),
    [
        (date(2026, 9, 1), "september 2026"),
        (date(2027, 1, 1), "jaanuar 2027"),
        (date(2031, 12, 1), "detsember 2031"),
    ],
)
def test_a_month_precision_row_prints_the_month_in_words(specialist, anchor, expected):
    _action(specialist, on=anchor, precision=DatePrecision.MONTH, title="Kuu täpsusega teema")

    printed = _items(specialist)["Kuu täpsusega teema"].short_date

    assert printed == expected
    assert printed == format_at_precision(anchor, DatePrecision.MONTH)


def test_no_two_number_month_and_no_day_in_its_place(specialist):
    """Both halves of §3.5 at once, on the string the row actually prints.

    The two-number form must be gone — that is the change (UQ-09) — and the
    anchor day must not have arrived in its place.
    """
    _action(specialist, on=date(2026, 9, 1), precision=DatePrecision.MONTH, title="Kuu täpsusega")

    printed = _items(specialist)["Kuu täpsusega"].short_date

    assert printed == "september 2026"
    assert not TWO_NUMBERS.match(printed)
    assert "09.26" not in printed
    assert "01.09" not in printed


def test_a_month_that_starts_today_is_still_a_month(specialist):
    """The other way to name a day, and the reason a period is answered first.

    A month anchors on its first, so on 1 September a September expectation
    would otherwise print *täna* — which tells the reader it is due today just
    as firmly as ``01.09.2026`` would.
    """
    first = date(2026, 9, 1)
    _action(specialist, on=first, precision=DatePrecision.MONTH, title="Algab täna")

    assert _items(specialist, today=first)["Algab täna"].short_date == "september 2026"


def test_an_overdue_month_names_the_month_beside_the_count(specialist):
    """The second line of the same cell speaks the same way.

    A passed month prints «N p üle» in the date cell and the meaning line under
    it carries the period it was, in the words the rest of the product uses.
    """
    _action(specialist, on=date(2026, 6, 1), precision=DatePrecision.MONTH, title="Möödas kuu")

    item = _items(specialist)["Möödas kuu"]

    assert item.short_date == "43 p üle"
    assert item.meaning_line == "PLAANIS juuni 2026"
    assert "06.26" not in item.meaning_line


# ---------------------------------------------------------------------------
# Every other precision is untouched
# ---------------------------------------------------------------------------


def test_an_exact_date_still_prints_as_a_day(specialist):
    _action(specialist, on=date(2026, 9, 15), precision=DatePrecision.EXACT, title="Täpne teema")

    item = _items(specialist)["Täpne teema"]

    assert item.short_date == "15.09"
    assert item.day_month == "15.09"
    assert item.weekday_letter == "T"
    assert not item.is_approximate


@pytest.mark.parametrize(
    ("anchor", "precision", "expected"),
    [
        (date(2027, 4, 1), DatePrecision.QUARTER, "II kvartal 2027"),
        (date(2027, 7, 1), DatePrecision.HALF_YEAR, "II poolaasta 2027"),
        (date(2028, 1, 1), DatePrecision.YEAR, "2028"),
    ],
)
def test_a_wider_period_keeps_the_words_it_had(specialist, anchor, precision, expected):
    """No ``04.27`` for a quarter, as there is none for a month any more."""
    _action(specialist, on=anchor, precision=precision, title="Lai periood")

    item = _items(specialist)["Lai periood"]

    assert item.short_date == expected
    assert item.display_date == expected


def test_a_response_deadline_is_a_day_and_stays_one(specialist):
    """``Matter.response_deadline`` is a ``DateField`` with no precision column.

    It can never be a month, and the row must not acquire a way of claiming it
    was one.
    """
    matter = _matter(specialist, title="Arvamuse tähtajaga teema")
    matter.response_deadline = date(2026, 9, 15)
    matter.save(update_fields=["response_deadline"])

    item = _items(specialist)["Arvamuse tähtajaga teema"]

    assert item.source_type == wi.SOURCE_RESPONSE_DEADLINE
    assert item.date_precision == DatePrecision.EXACT
    assert item.short_date == "15.09"
    assert not item.is_approximate


# ---------------------------------------------------------------------------
# An Oluline tähtaeg is the other source that can hold a month
# ---------------------------------------------------------------------------


def test_an_important_date_recorded_to_a_month_reads_the_same_way(specialist):
    """§A4: the two sources that can hold a month spell it identically.

    A milestone and a next action are different obligations sharing one column,
    and a reader comparing two rows must not have to know which table each came
    from in order to read the date.
    """
    matter = _matter(specialist, title="Olulise tähtajaga teema")
    start, end = period_bounds(date(2026, 9, 1), DatePrecision.MONTH)
    add_important_date(
        matter=matter,
        title="Eelnõu esitamine",
        date_value=start,
        period_end=end,
        date_precision=DatePrecision.MONTH,
        actor=specialist,
    )

    item = _items(specialist)["Olulise tähtajaga teema"]

    assert item.source_type == wi.SOURCE_IMPORTANT_DEADLINE
    assert item.short_date == "september 2026"
    assert item.display_date == "september 2026"


# ---------------------------------------------------------------------------
# Nothing but the string moved
# ---------------------------------------------------------------------------


def test_the_month_keeps_its_anchor_period_band_and_overdue_reading(specialist):
    """A display decision that had reached the sorting would be a bug rather
    than a UI change. Every value the list is built out of, asserted against the
    stored month rather than against the printed string."""
    anchor = date(2026, 9, 1)
    _action(specialist, on=anchor, precision=DatePrecision.MONTH, title="Kuu täpsusega")

    item = _items(specialist)["Kuu täpsusega"]

    assert item.when == anchor
    assert item.period_end == date(2026, 9, 30)
    assert item.date_precision == DatePrecision.MONTH
    assert item.is_approximate
    assert not item.is_overdue
    assert not item.is_review_ripe
    # A weekday would name a day exactly the way the date would.
    assert item.weekday_letter == ""
    # *Järgmised 30 päeva* takes day-precise dates only, so a month inside the
    # window is *Hiljem*.
    assert wi.band_of(item, TODAY, WEEK_END, None) == wi.BAND_LATER


def test_the_cell_counts_days_from_the_last_day_of_the_month(specialist):
    """The period, not the anchor. 30 September is still inside September.

    `days_late` is what the cell prints and it reads `period_end`, so the month
    is still spelled out on its last day and becomes «1 p üle» the morning after.
    """
    _action(specialist, on=date(2026, 9, 1), precision=DatePrecision.MONTH, title="Kuu täpsusega")

    last_day = _items(specialist, today=date(2026, 9, 30))["Kuu täpsusega"]
    assert last_day.period_end == date(2026, 9, 30)
    assert last_day.days_late == 0
    assert last_day.short_date == "september 2026"

    after = _items(specialist, today=date(2026, 10, 1))["Kuu täpsusega"]
    assert after.days_late == 1
    assert after.short_date == "1 p üle"
    assert after.meaning_line == "PLAANIS september 2026"


def test_the_page_builder_puts_the_month_where_it_always_was(specialist):
    """The bands the page is actually assembled from, on a fixed day."""
    _action(specialist, on=date(2026, 9, 1), precision=DatePrecision.MONTH, title="Ribas kuu")

    bands = {band.key: band for band in build_my_work(specialist, today=TODAY).bands}

    assert [item.matter.title for item in bands[wi.BAND_LATER].items] == ["Ribas kuu"]
    assert wi.BAND_NEXT_30 not in bands


# ---------------------------------------------------------------------------
# The page, and the other page that renders the same row
# ---------------------------------------------------------------------------


def _next_month_first() -> date:
    """The first of next month — inside the page's default two-month horizon."""
    today = timezone.localdate()
    return (today.replace(day=1) + timedelta(days=45)).replace(day=1)


def _compact(anchor: date) -> str:
    return f"{anchor.month:02d}.{anchor.year % 100:02d}"


def test_the_rendered_work_row_writes_the_month_out(client, specialist):
    """Through the view, so the template is proved and not only the read model."""
    anchor = _next_month_first()
    _action(specialist, on=anchor, precision=DatePrecision.MONTH, title="Renderdatud kuu")
    client.force_login(specialist)

    html = client.get(MY_WORK).content.decode()

    assert "Renderdatud kuu" in html
    assert format_at_precision(anchor, DatePrecision.MONTH) in html
    assert f">{_compact(anchor)}</span>" not in html
    assert f"01.{anchor.month:02d}.{anchor.year}" not in html


def test_a_colleagues_page_spells_the_month_the_same_way(client, specialist, department_head):
    """§A4: one row template, two pages, one representation."""
    anchor = _next_month_first()
    _action(specialist, on=anchor, precision=DatePrecision.MONTH, title="Kolleegi kuu")
    client.force_login(department_head)

    url = reverse("matters:person_work", kwargs={"pk": specialist.pk})
    html = client.get(url).content.decode()

    assert "Kolleegi kuu" in html
    assert format_at_precision(anchor, DatePrecision.MONTH) in html
    assert f">{_compact(anchor)}</span>" not in html
