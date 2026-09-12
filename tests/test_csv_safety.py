"""An exported cell is text, not a program.

Adversarial QA on 12 September found that a Teema title typed as
``=HYPERLINK(...)`` reaches the CSV verbatim, and a spreadsheet reads a cell
beginning with ``=``, ``+``, ``-`` or ``@`` as a formula (QA-15). The
reproduction needed an internal author to type it, so this is defence in depth
rather than a route in from outside — but an export is pasted into a board paper,
mailed on, and opened on a machine where nothing about its provenance is visible.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.urls import reverse

from app.core.csv_safety import csv_safe, csv_safe_row
from app.matters.enums import MatterOrigin
from tests import factories

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "typed",
    [
        '=HYPERLINK("http://paha.example","Vaata")',
        "+SUM(A1:A9)",
        "-cmd|' /c calc'!A0",
        "@SUM(1+1)*cmd",
        '\t=HYPERLINK("http://paha.example")',
        "\r+SUM(A1)",
        "\n-cmd",
        "=",
    ],
)
def test_a_formula_leading_cell_is_made_inert(typed):
    """Prefixed, and otherwise unchanged: the reader still sees what was typed."""
    safe = csv_safe(typed)

    assert safe == f"'{typed}"
    assert safe[1:] == typed


@pytest.mark.parametrize(
    "ordinary",
    [
        "Pakendiseaduse muutmise seaduse eelnõu",
        "Vastus esitatud ja järeltegevus tehtud",
        "koda.ee/kaasamine",
        "",
        "2026-09-12",
        "1 = 1",
        " =SUM(A1)",
    ],
)
def test_an_ordinary_cell_is_returned_byte_for_byte(ordinary):
    """Including a leading space before an `=`.

    A spreadsheet does not evaluate that one, and prefixing every cell that
    contains the character somewhere would put an apostrophe in front of half the
    export.
    """
    assert csv_safe(ordinary) == ordinary


@pytest.mark.parametrize("number", ["-5", "-3.2", "-3,2", "+7", "12"])
def test_a_negative_number_written_as_text_stays_a_number(number):
    """`-` is on the dangerous list, and a negative figure begins with one.

    Prefixing it would turn a numeric column into text in every spreadsheet that
    opens the file, which is the fix costing more than the defect.
    """
    assert csv_safe(number) == number


@pytest.mark.parametrize(
    "value", [5, -5, 0, 3.25, Decimal("-9.5"), dt.date(2026, 9, 12), None, True]
)
def test_a_non_string_is_never_touched(value):
    """`csv.writer` renders these, and nothing here has made them text."""
    assert csv_safe(value) is value


def test_a_row_is_every_cell_of_it():
    row = ["Pakendiseadus", "=SUM(A1)", 42, None, "-cmd"]

    assert csv_safe_row(row) == ["Pakendiseadus", "'=SUM(A1)", 42, None, "'-cmd"]


# ---------------------------------------------------------------------------
# The exports themselves
# ---------------------------------------------------------------------------


def _export(client, slug: str) -> str:
    """`periood=koik`, so the export's population is not the current year alone.

    The reporting surfaces default to a period, and a Matter created by a factory
    lands wherever its `reporting_year` says — so an export asked without this
    legitimately answers with a header row and nothing under it.
    """
    response = client.get(reverse("reporting:export", kwargs={"slug": slug}), {"periood": "koik"})
    assert response.status_code == 200
    return b"".join(response.streaming_content).decode("utf-8-sig")


def test_a_formula_title_leaves_the_export_inert(signed_in, specialist):
    """End to end, through the route a person downloads."""
    factories.MatterFactory(
        owner=specialist,
        title='=HYPERLINK("http://paha.example","Vaata")',
        origin=MatterOrigin.NATIVE,
    )

    body = _export(signed_in, "teemad")

    assert "'=HYPERLINK" in body
    # And no cell begins a formula. The quoting is `QUOTE_MINIMAL`, so a cell
    # carrying the delimiter is wrapped — the apostrophe sits inside the quotes.
    for line in body.splitlines():
        for cell in line.split(";"):
            assert not cell.lstrip('"').startswith(("=", "@")), cell


def test_an_ordinary_title_is_unchanged_in_the_export(signed_in, specialist):
    """The other half: nothing was added to a cell that did not need it."""
    factories.MatterFactory(
        owner=specialist,
        title="Pakendiseaduse muutmise seaduse eelnõu",
        origin=MatterOrigin.NATIVE,
    )

    body = _export(signed_in, "teemad")

    assert "Pakendiseaduse muutmise seaduse eelnõu" in body
    assert "'Pakendiseaduse" not in body
