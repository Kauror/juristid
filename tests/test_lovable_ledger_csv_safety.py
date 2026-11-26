"""`ledger.csv` is read by a spreadsheet, so no cell in it runs (SEC-007).

`lovable_inventory.ledger_csv` wrote values read from the Lovable register —
references, notes, candidate labels, all typed by somebody outside this system
— through a bare `csv.writer`. A note beginning with ``=`` became a formula the
moment the file was opened in Excel or LibreOffice. Every other CSV this
application writes already went through `app.core.csv_safety.csv_safe_row`;
this one now does too. `ledger.json`, written beside it, is the
machine-readable record and keeps every value exactly as the source held it.
"""

from __future__ import annotations

import csv
import io

import pytest

from app.legacy_import.lovable_inventory import CSV_COLUMNS, Ledger, LedgerRow, ledger_csv


def _row(**overrides) -> LedgerRow:
    values = {
        "source_id": "11111111-1111-1111-1111-111111111111",
        "reference": "2026_001",
        "year": 2026,
        "operational": True,
        "activity": "ACTIVE",
        "category": "EXACT",
        "reason": "",
    }
    values.update(overrides)
    return LedgerRow(**values)


def _cells(ledger: Ledger) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(ledger_csv(ledger))))


def _ledger(*rows: LedgerRow) -> Ledger:
    return Ledger(taken_at="2026-11-26T00:00:00Z", source_sha256="0" * 64, rows=list(rows))


@pytest.mark.parametrize(
    "value",
    [
        '=HYPERLINK("https://example.invalid","vaata")',
        "+1+cmd|' /C calc'!A0",
        "-2+3",
        "@SUM(A1:A9)",
        "\t=1+1",
        "\r=1+1",
        "\n=1+1",
        " =1+1",
        " =1+1",
        "​=1+1",
        "﻿@x",
        "\x00=1",
    ],
)
def test_a_formula_from_the_source_is_text_in_every_spreadsheet_bound_column(value: str):
    row = _row(
        reference=value,
        reason=value,
        notes=[value],
        archive_pages=[value],
        cohort=value,
        stage_basis=value,
        matter_reference=value,
        candidates=[{"basis": value, "reference": "", "source_id": "x"}],
    )

    [cells] = _cells(_ledger(row))

    for column in (
        "reference",
        "reason",
        "notes",
        "archive_pages",
        "cohort",
        "stage_basis",
        "matter_reference",
        "candidates",
    ):
        assert cells[column].startswith("'"), (column, cells[column])
        assert cells[column][1:] == (f"{value}:x" if column == "candidates" else value), column


def test_ordinary_text_and_unicode_are_untouched():
    row = _row(
        reference="2026_042",
        reason="Õiguskantsleri märkus — «täpsustada»",
        notes=["Eelnõu 15-SE", "ühe-kahe nädala jooksul"],
        stage_basis="kooskõlastus",
    )

    [cells] = _cells(_ledger(row))

    assert cells["reference"] == "2026_042"
    assert cells["reason"] == "Õiguskantsleri märkus — «täpsustada»"
    assert cells["notes"] == "Eelnõu 15-SE ühe-kahe nädala jooksul"
    assert cells["stage_basis"] == "kooskõlastus"


def test_genuine_numbers_booleans_and_blanks_stay_what_they_are():
    row = _row(
        reference="-42",
        reason="3,5",
        year=2026,
        operational=False,
        stage_mismatch=None,
        open_closed_mismatch=True,
    )

    [cells] = _cells(_ledger(row))

    assert cells["reference"] == "-42"  # a negative number is not a formula
    assert cells["reason"] == "3,5"
    assert cells["year"] == "2026"
    assert cells["operational"] == "False"
    assert cells["stage_mismatch"] == ""
    assert cells["open_closed_mismatch"] == "True"


def test_the_header_and_column_order_are_unchanged():
    text = ledger_csv(_ledger(_row()))

    assert text.splitlines()[0] == ",".join(CSV_COLUMNS)


def test_the_machine_readable_ledger_keeps_the_source_value():
    """`ledger.json` is written from the rows themselves, never from the CSV."""
    value = '=IMPORTXML("https://example.invalid","//a")'
    ledger = _ledger(_row(reason=value, notes=[value]))

    document = ledger.document()

    [stored] = document["rows"]
    assert stored["reason"] == value
    assert stored["notes"] == [value]
