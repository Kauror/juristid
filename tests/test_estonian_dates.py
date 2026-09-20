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

import ast
import re
from datetime import date
from pathlib import Path

import pytest

from app.core.dates import (
    ESTONIAN_DATE_ERROR,
    format_estonian_date,
    parse_estonian_date,
    parse_flexible_date,
)
from app.core.widgets import EstonianDateField, EstonianDateInput

#: This file is `tests/`, so the repository is its parent.
REPO_ROOT = Path(__file__).resolve().parents[1]

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
        # Both components zero-padded in ISO and neither padded here: the case
        # a `%-d.%-m.%Y` was reached for, and the reason the guard below exists.
        (date(2026, 2, 7), "7.2.2026"),
        (date(2026, 11, 21), "21.11.2026"),
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


# ---------------------------------------------------------------------------
# `1 kuu` is a calendar month, and the end of one is where that is decided
# ---------------------------------------------------------------------------
#
# `Tagasisidet ootame kuni` offers `1 kuu` beside `1 nädal` and `2 nädalat`
# (docs/adr/0086 §2). The first two are spans in days and need no arithmetic of
# their own; the third cannot be one, because somebody picking it means «the
# same day next month» and not «thirty days».
#
# The only place the two readings disagree is the end of a month, and the rule
# there is the one every calendar keeps: there is no 31 February to land on, so
# it clamps.


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        # The ordinary case: the day of the month is kept.
        (date(2026, 9, 16), 1, date(2026, 10, 16)),
        # Across a year boundary, where a naive `month + 1` produces month 13.
        (date(2026, 12, 31), 1, date(2027, 1, 31)),
        # The clamp, in a common year and in a leap year.
        (date(2027, 1, 31), 1, date(2027, 2, 28)),
        (date(2028, 1, 31), 1, date(2028, 2, 29)),
        # A 31-day month into a 30-day one.
        (date(2026, 10, 31), 1, date(2026, 11, 30)),
        # The last day of February is not «the last day» of March.
        (date(2027, 2, 28), 1, date(2027, 3, 28)),
        # Several months, and backwards, because the helper takes a number.
        (date(2026, 9, 16), 4, date(2027, 1, 16)),
        (date(2026, 3, 31), -1, date(2026, 2, 28)),
    ],
)
def test_a_calendar_month_keeps_the_day_or_clamps_to_the_shorter_month(start, months, expected):
    from app.core.dates import add_months

    assert add_months(start, months) == expected


def test_a_month_is_not_thirty_days():
    """The claim `1 kuu` makes, stated as the difference it is there for.

    Both starts are in 31-day months, which is where the two readings part: a
    round opened on 15 May collects until 15 June, and thirty days would say the
    14th. January is the sharper one — the clamp and the span disagree by three
    days.
    """
    from datetime import timedelta

    from app.core.dates import add_months

    assert add_months(date(2026, 1, 31), 1) != date(2026, 1, 31) + timedelta(days=30)
    assert add_months(date(2026, 5, 15), 1) != date(2026, 5, 15) + timedelta(days=30)


# ---------------------------------------------------------------------------
# The format may not be rebuilt with a platform-specific directive
# ---------------------------------------------------------------------------

#: The directives that mean "drop the leading zero", and the reason
#: `format_estonian_date` is written by hand instead of with `strftime`.
#:
#: `%-d` is glibc and `%#d` is the Microsoft CRT. Neither is in the C standard,
#: so `strftime` raises `ValueError: Invalid format string` for the other one's
#: spelling — and this repository is developed on Windows and deployed on Linux,
#: so either spelling is a module one of the two platforms cannot *import*.
#:
#: That is what makes it worth a guard rather than a code review note. On
#: 2026-09-20 `e2e/test_procedural_development_correction.py` carried a
#: `%-d.%-m.%Y` at module scope: Linux CI was green, and on Windows the
#: `ValueError` fired during **collection**, which aborts the entire `e2e`
#: population rather than failing one test. The suite could not be run locally
#: at all without `--ignore`, and nothing in CI could ever say so.
NON_PORTABLE_STRFTIME = ("%-", "%#")

#: Where a date format could plausibly be built.
_SCANNED = ("app", "tests", "e2e")


def _strftime_formats() -> list[tuple[Path, int, str]]:
    """Every literal format string handed to a ``.strftime(...)`` call.

    Parsed rather than searched, and that distinction is the whole reliability
    of this test: `app/core/dates.py` and `tests/test_teema_ux_consolidation.py`
    both *name* `%-d` and `%#d` in prose explaining why they are avoided, and a
    grep-shaped guard would fail on the documentation of its own rule.

    Only literal first arguments are visible here. A format held in a variable
    is not checked, which is an honest limit rather than a gap worth closing
    with a heuristic: the defect this exists for was written inline, and so is
    every date format in this repository today.
    """
    found: list[tuple[Path, int, str]] = []
    for directory in _SCANNED:
        for path in sorted((REPO_ROOT / directory).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute) or func.attr != "strftime":
                    continue
                if not node.args:
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found.append((path, node.lineno, first.value))
    return found


def test_no_strftime_uses_a_platform_specific_directive() -> None:
    """Use `format_estonian_date`, not a directive one platform refuses.

    Zero-padded `%d.%m.%Y` is portable and stays allowed — it is what a date box
    is *filled with*. What is refused is the unpadded spelling, which is what the
    page *reads back*, and which `format_estonian_date` already produces.
    """
    offenders = [
        f"{path.relative_to(REPO_ROOT).as_posix()}:{line}: {fmt!r}"
        for path, line, fmt in _strftime_formats()
        if any(token in fmt for token in NON_PORTABLE_STRFTIME)
    ]
    assert not offenders, (
        "strftime formats that one of Linux/Windows refuses to parse — use "
        "app.core.dates.format_estonian_date instead:\n" + "\n".join(offenders)
    )


def test_the_guard_can_actually_see_a_strftime_format() -> None:
    """The guard is worthless if its parser quietly finds nothing.

    A scan that returns an empty list passes the test above for the wrong
    reason, and would go on passing after a refactor moved every date format out
    of its reach.
    """
    formats = _strftime_formats()
    assert formats, "the strftime scan found no format strings at all"
    assert any("%d.%m.%Y" == fmt for _path, _line, fmt in formats), (
        "expected the portable zero-padded format to be among those scanned"
    )
