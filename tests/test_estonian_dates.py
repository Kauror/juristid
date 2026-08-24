"""One date format reaches the reader, and it is the one Estonians write.

The defect this covers was invisible on the machine that built the software. A
native ``<input type="date">`` renders in the *browser's* locale: on a Estonian
Chrome it looked right, and on a US-English Windows the same page offered
``mm/dd/yyyy`` and read ``7.9.2026`` as the 9th of July. Nothing server-side can
reach inside that control, so the control had to go.

Storage is deliberately untested here beyond one assertion, because storage did
not change: the columns are still ``DateField`` and still hold ISO.
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from app.core.dates import (
    ESTONIAN_DATE_ERROR,
    format_estonian_date,
    parse_estonian_date,
    parse_flexible_date,
)
from app.core.widgets import EstonianDateField, EstonianDateInput

#: What must never reach an Estonian screen. The first is what a US browser
#: renders; the second is what the database speaks.
US_PLACEHOLDER = "mm/dd/yyyy"
ISO_SHAPE = re.compile(r"\d{4}-\d{2}-\d{2}")


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (date(2026, 9, 7), "7.9.2026"),
        (date(2026, 8, 23), "23.8.2026"),
        (date(2027, 1, 1), "1.1.2027"),
        (date(2026, 12, 31), "31.12.2026"),
    ],
)
def test_a_date_is_written_day_month_year_without_padding(value, expected):
    assert format_estonian_date(value) == expected


def test_nothing_renders_as_an_empty_string_not_as_the_word_none():
    assert format_estonian_date(None) == ""


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("7.9.2026", date(2026, 9, 7)),
        ("07.09.2026", date(2026, 9, 7)),
        ("23.8.2026", date(2026, 8, 23)),
        ("  7.9.2026  ", date(2026, 9, 7)),
        ("7.9.26", date(2026, 9, 7)),
    ],
)
def test_the_ways_somebody_actually_types_a_date_all_parse(typed, expected):
    assert parse_estonian_date(typed) == expected


@pytest.mark.parametrize("typed", ["31.02.2026", "0.9.2026", "7.13.2026", "32.1.2026"])
def test_an_impossible_date_is_refused_rather_than_approximated(typed):
    """31.02 is somebody mistyping, and the 28th is not what they meant."""
    assert parse_estonian_date(typed) is None


@pytest.mark.parametrize("typed", ["09/07/2026", "9/7/2026", "2026.09.07", "eile", ""])
def test_an_ambiguous_or_unreadable_value_is_refused(typed):
    """`09/07/2026` means September in one country and July in another.

    A system that picks one is wrong half the time without ever saying so.
    """
    assert parse_estonian_date(typed) is None


def test_iso_is_still_read_where_a_url_might_carry_it():
    """Links, bookmarks and saved queries from before this module carry ISO."""
    assert parse_flexible_date("2026-09-07") == date(2026, 9, 7)
    assert parse_flexible_date("7.9.2026") == date(2026, 9, 7)
    assert parse_flexible_date("09/07/2026") is None


# ---------------------------------------------------------------------------
# The control
# ---------------------------------------------------------------------------


def test_the_date_control_is_not_a_native_date_input():
    """The one assertion that would have caught the production defect."""
    rendered = EstonianDateInput().render("tahtaeg", date(2026, 9, 7))
    assert 'type="text"' in rendered
    assert 'type="date"' not in rendered


def test_the_control_shows_the_value_the_estonian_way():
    rendered = EstonianDateInput().render("tahtaeg", date(2026, 9, 7))
    assert 'value="7.9.2026"' in rendered
    assert not ISO_SHAPE.search(rendered)


def test_the_control_promises_the_format_it_accepts():
    rendered = EstonianDateInput().render("tahtaeg", None)
    assert 'placeholder="pp.kk.aaaa"' in rendered
    assert US_PLACEHOLDER not in rendered


def test_the_control_asks_for_the_calendar_the_application_owns():
    """Without this hook the box is a bare text field and the picker is gone."""
    assert "data-datepicker" in EstonianDateInput().render("tahtaeg", None)


def test_a_refused_value_is_shown_back_as_it_was_typed():
    """A blank box under "correct this" loses what the person wrote."""
    assert 'value="9/7/2026"' in EstonianDateInput().render("tahtaeg", "9/7/2026")


# ---------------------------------------------------------------------------
# The form field
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("typed", ["7.9.2026", "07.09.2026", "2026-09-07"])
def test_the_field_cleans_every_accepted_form_to_one_date(typed):
    assert EstonianDateField(required=False).clean(typed) == date(2026, 9, 7)


def test_the_field_refuses_an_impossible_date_in_estonian():
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError) as refusal:
        EstonianDateField(required=False).clean("31.02.2026")
    assert refusal.value.messages == [ESTONIAN_DATE_ERROR]
    # The message says what to type rather than what was wrong with what was
    # typed: "Sisesta korrektne kuupäev" leaves the reader no wiser.
    assert "7.9.2026" in ESTONIAN_DATE_ERROR


def test_the_field_stores_a_real_date_object():
    """Presentation changed; storage did not."""
    cleaned = EstonianDateField(required=False).clean("7.9.2026")
    assert isinstance(cleaned, date)
    assert cleaned.isoformat() == "2026-09-07"
