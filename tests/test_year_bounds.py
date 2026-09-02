"""A year arriving from a URL is a year, on every surface that reads one.

CORR-02. Six parameters across seven surfaces took a year out of a query
string, checked that it looked like a number, and handed it to the ORM. A value
outside the range this application supports then reached a `date()` constructor
and raised — so `?periood=0`, `?aasta=99999` and `?suletud=12345678901234567890`
were 500s on pages whose own docstrings promise that a hand-edited URL falls
back rather than producing a stack trace.

Three mechanisms, all of them the same missing check:

* `date(year, 1, 1)` raises `ValueError` below year 1 and above 9999;
* Django's `__year` lookup is compiled to a *range*, so `sent_at__year=9999`
  builds `date(10000, 1, 1)` and raises on the year **after** one that is
  itself in range;
* a value with more digits than a C long raises `OverflowError` rather than
  `ValueError`, so a `except ValueError` around the parse would not have caught
  it either.

The range itself is not new. `app.workflow.dates` has owned it since Stage 2G —
1990 to 2100, wide enough for a 2011 register row and a transposition deadline
somebody has heard about — and two dated forms already enforce it. What was
missing is that a query string has no form to refuse it.

**What each surface does with an unreadable year is deliberately different**, and
this file pins those differences rather than flattening them. Statistika falls
back to its default period, Jälgimine drops the filter, the register empties the
list so the rows cannot contradict the chip above them, the Arvamused workspace
refuses with a sentence, and the statistics drill-through 404s because arriving
there with an unreadable year means the link was edited. Four settled answers to
one question; none of them is a 500.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from app.reporting.context import DEFAULT_PERIOD, parse_period
from app.submissions.workspace import SentFilters, SubmissionQueryRefused, sent_queryset
from app.workflow.dates import MAX_YEAR, MIN_YEAR, year_from
from tests import factories

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# The reader itself
# ---------------------------------------------------------------------------

#: Values that are not a supported year, and the reason each one is here.
NOT_A_YEAR = [
    ("", "empty"),
    ("   ", "whitespace"),
    ("abc", "a word"),
    ("2O24", "a letter O for a zero — the typo the register chips exist to expose"),
    ("-5", "negative"),
    ("0", "below any calendar this product uses"),
    ("1", "year 1 AD: readable as a number, absurd as a filter"),
    ("7", "what `?periood=7` means on Osakond, where it is seven *days*"),
    (str(MIN_YEAR - 1), "one below the supported range"),
    (str(MAX_YEAR + 1), "one above the supported range"),
    ("9999", "in `date()`'s range but not in ours, and `__year` needs 10000"),
    ("99999", "outside `date()`'s range entirely"),
    ("12345678901234567890", "raises OverflowError rather than ValueError"),
    ("²", "isdigit() is True and int() raises — the trap in the obvious check"),
    ("٣", "an Arabic-Indic digit: int() succeeds, which is worse"),
    ("2026.0", "not an integer"),
]


@pytest.mark.parametrize(
    "raw",
    [value for value, _ in NOT_A_YEAR],
    ids=[reason for _, reason in NOT_A_YEAR],
)
def test_year_from_refuses_everything_that_is_not_a_supported_year(raw):
    assert year_from(raw) is None


@pytest.mark.parametrize("year", [MIN_YEAR, 2011, 2026, MAX_YEAR])
def test_year_from_accepts_the_supported_range(year):
    assert year_from(str(year)) == year


def test_year_from_reads_a_padded_value_and_takes_none_for_nothing():
    assert year_from("  2026  ") == 2026
    assert year_from(None) is None


# ---------------------------------------------------------------------------
# Statistika — the period falls back
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [value for value, _ in NOT_A_YEAR])
def test_an_unreadable_period_is_the_default_rather_than_an_error(raw, world):
    from datetime import date

    period = parse_period(raw, date(2026, 9, 1))
    assert period.key == DEFAULT_PERIOD
    # And every derived value it will be asked for is constructible. This is the
    # assertion the defect actually failed: the Period was built happily and
    # raised later, inside a selector, on a page that had already started.
    assert period.start_date is not None
    assert period.end_datetime() is not None
    assert len(period.years) <= 1


def test_a_readable_period_still_works_in_both_forms(world):
    from datetime import date

    today = date(2026, 9, 1)
    assert parse_period("2024", today).start_year == 2024
    span = parse_period("2024-2026", today)
    assert (span.start_year, span.end_year) == (2024, 2026)
    assert span.years == [2024, 2025, 2026]


def test_a_reversed_or_half_unreadable_span_falls_back(world):
    from datetime import date

    today = date(2026, 9, 1)
    assert parse_period("2026-2024", today).key == DEFAULT_PERIOD
    assert parse_period("2024-99999", today).key == DEFAULT_PERIOD
    assert parse_period("99999-2024", today).key == DEFAULT_PERIOD


# ---------------------------------------------------------------------------
# Every surface that reads a year, end to end
# ---------------------------------------------------------------------------

#: `(url, parameter)`, and the status an unreadable value must produce. Not one
#: rule: each surface answers "what should a nonsense filter do here" its own
#: way, and the only shared requirement is that none of them is a 500.
YEAR_SURFACES = [
    ("/statistika/", "periood", 200),
    ("/statistika/teemad/", "periood", 200),
    ("/statistika/tegevus/", "periood", 200),
    ("/statistika/arvamused/", "periood", 200),
    ("/statistika/ajalooline/", "periood", 200),
    ("/statistika/andmekvaliteet/", "periood", 200),
    # The drill-through refuses instead: arriving with a year that cannot be
    # read means the link was edited, and every year is not the answer.
    ("/statistika/arvamused/", "aasta", 404),
    ("/teemad/", "suletud", 200),
    ("/jalgimine/tahtajad/", "aasta", 200),
    ("/jalgimine/joustumised/", "aasta", 200),
    ("/jalgimine/toovoidud/", "aasta", 200),
    ("/arvamused/", "aasta", 200),
]

#: The subset worth firing at a real view. The unit tests above cover the rest;
#: this is about what the *page* does.
CRAFTED = ["0", "1", "7", "9999", "99999", "12345678901234567890", "-5", "abc", "²"]


@pytest.mark.parametrize(("path", "param", "expected"), YEAR_SURFACES)
@pytest.mark.parametrize("value", CRAFTED)
def test_a_crafted_year_never_reaches_a_date_constructor(
    client, department_head, world, path, param, expected, value
):
    client.force_login(department_head)
    response = client.get(path, {param: value})
    assert response.status_code == expected, (
        f"{path}?{param}={value} answered {response.status_code}; "
        "an unreadable year must not become a server error"
    )


@pytest.mark.parametrize(("path", "param", "expected"), YEAR_SURFACES)
def test_a_readable_year_is_still_accepted_everywhere(
    client, department_head, world, path, param, expected
):
    """The bound narrows what is refused; it must not narrow what works."""
    client.force_login(department_head)
    assert client.get(path, {param: "2026"}).status_code == 200


# ---------------------------------------------------------------------------
# The refusals each surface makes are its own, and are preserved
# ---------------------------------------------------------------------------


def test_the_register_empties_the_list_rather_than_ignoring_the_filter(client, specialist, world):
    """`?suletud=` is the register's rule: unreadable means no rows, not all rows."""
    factories.MatterFactory(
        owner=specialist,
        title="Suletud aasta kontroll",
        reference_year=2099,
        reference_number=901,
    )
    client.force_login(specialist)

    everything = client.get(reverse("matters:matter_list"))
    crafted = client.get(reverse("matters:matter_list"), {"suletud": "99999"})

    assert everything.status_code == crafted.status_code == 200
    assert crafted.context["page"].paginator.count == 0
    assert everything.context["page"].paginator.count > 0


def test_the_arvamused_workspace_refuses_with_a_sentence_naming_the_range(specialist, world):
    with pytest.raises(SubmissionQueryRefused) as refusal:
        sent_queryset(specialist, SentFilters(year="99999"))

    message = str(refusal.value)
    assert str(MIN_YEAR) in message and str(MAX_YEAR) in message


def test_jalgimine_drops_an_unreadable_year_and_shows_everything(client, department_head, world):
    """The filter is dropped, which its own docstring already promised."""
    client.force_login(department_head)

    unfiltered = client.get("/jalgimine/toovoidud/")
    crafted = client.get("/jalgimine/toovoidud/", {"aasta": "99999"})

    assert unfiltered.status_code == crafted.status_code == 200
    assert crafted.context["page"].paginator.count == unfiltered.context["page"].paginator.count
