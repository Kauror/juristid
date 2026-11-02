"""`app.core.xlsx_safety`: every cell typed explicitly, nothing evaluated."""

from __future__ import annotations

import io
from datetime import UTC, date, datetime

import openpyxl
import pytest

from app.core.xlsx_safety import (
    Column,
    SheetWriter,
    info_sheet,
    new_workbook,
    sheet_title,
    workbook_bytes,
    workbook_response,
)


def _roundtrip(rows, columns=("A", "B", "C")):
    workbook = new_workbook()
    writer = SheetWriter(workbook, "2026", [Column(c) for c in columns])
    for row in rows:
        writer.append(row)
    writer.finish()
    return openpyxl.load_workbook(io.BytesIO(workbook_bytes(workbook)))["2026"]


@pytest.mark.parametrize(
    "value", ['=HYPERLINK("https://example.invalid")', "+1+1", "@SUM(A1)", " \t=1", "-cmd"]
)
def test_a_formula_looking_text_is_stored_as_text(value):
    sheet = _roundtrip([[value, None, None]])

    cell = sheet["A2"]
    assert cell.value == value
    assert cell.data_type == "s"
    assert cell.quotePrefix


def test_types_are_explicit():
    sheet = _roundtrip([["2026_014", date(2026, 3, 4), 7]])

    assert (sheet["A2"].value, sheet["A2"].data_type) == ("2026_014", "s")
    assert sheet["B2"].is_date and sheet["B2"].value.date() == date(2026, 3, 4)
    assert sheet["B2"].number_format == "dd.mm.yyyy"
    assert (sheet["C2"].value, sheet["C2"].data_type) == (7, "n")


def test_an_aware_timestamp_is_written_as_its_tallinn_day():
    # 22:30 UTC on 1 March is 00:30 on 2 March in Tallinn.
    sheet = _roundtrip([[datetime(2026, 3, 1, 22, 30, tzinfo=UTC), None, None]])

    assert sheet["A2"].value.date() == date(2026, 3, 2)


def test_estonian_text_header_freeze_and_filter():
    sheet = _roundtrip([["Õigusakt šõäöü", None, None]], columns=("Pealkiri", "B", "C"))

    assert sheet["A1"].value == "Pealkiri" and sheet["A1"].font.bold
    assert sheet["A2"].value == "Õigusakt šõäöü"
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref == "A1:C2"


def test_sheet_titles_are_made_acceptable():
    assert sheet_title("2026") == "2026"
    assert sheet_title("a/b:c") == "a b c"
    assert len(sheet_title("x" * 50)) == 31


def test_the_response_is_private_and_named():
    workbook = new_workbook()
    info_sheet(workbook, "Teave", ["Rida"])

    response = workbook_response(workbook, "teemad õigus.xlsx")

    assert response["Cache-Control"] == "private, no-store"
    assert "attachment" in response["Content-Disposition"]
    assert "filename*=" in response["Content-Disposition"]
    assert openpyxl.load_workbook(io.BytesIO(response.content)).sheetnames == ["Teave"]
