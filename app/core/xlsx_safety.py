"""Writing business data to an Excel workbook without letting a cell run.

The XLSX twin of `app.core.csv_safety`. In a workbook the danger is sharper than
in a CSV: openpyxl itself stores any string that begins with ``=`` as a formula
(``data_type = "f"``), so a Teema titled ``=HYPERLINK(...)`` would be written as
a live formula before Excel ever sees it. Every text cell here is therefore
written with an explicit string type, and a formula-looking one also carries
Excel's own «quote prefix» style, so the cell shows exactly what was typed and
is never evaluated. Nothing is dropped or truncated.

Dates are written as real dates (``dd.mm.yyyy``) only when they *are* exact days;
an approximate or unknown date is the caller's text (`format_at_precision`), so
a month never becomes its first day. Numbers are numbers. Identifiers and
references are text, so ``2026_14`` and a leading zero survive.

`workbook_response` streams the file privately: ``Cache-Control: private,
no-store``, an RFC 6266 ``Content-Disposition`` (`app.core.http`), and nothing
left on disk — the workbook is built in a write-only (constant-memory) sheet
and saved to an in-memory buffer.
"""

from __future__ import annotations

import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from django.http import HttpResponse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet._write_only import WriteOnlyWorksheet

from app.core.csv_safety import FORMULA_PREFIXES, _leading
from app.core.http import content_disposition

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: Excel's own limit; a longer string corrupts the file rather than truncating.
CELL_LIMIT = 32_767

#: Excel refuses sheet titles longer than this or containing these characters.
SHEET_TITLE_LIMIT = 31
_SHEET_FORBIDDEN = set("[]:*?/\\")

DATE_FORMAT = "dd.mm.yyyy"

_HEADER_FONT = Font(bold=True)
_WRAP = Alignment(wrap_text=True, vertical="top")
_TOP = Alignment(vertical="top")


def looks_like_formula(value: str) -> bool:
    """Whether a spreadsheet would read ``value`` as the start of a formula."""
    first = _leading(value)
    return bool(first) and first[0] in FORMULA_PREFIXES


def sheet_title(text: str) -> str:
    """A worksheet name Excel accepts, from a year or a label."""
    clean = "".join(" " if ch in _SHEET_FORBIDDEN else ch for ch in str(text)).strip()
    return (clean or "Leht")[:SHEET_TITLE_LIMIT]


@dataclass(frozen=True)
class Column:
    """One worksheet column: its heading, a width in characters, and wrapping."""

    heading: str
    width: int = 18
    wrap: bool = False


class SheetWriter:
    """Rows into one write-only worksheet, every value typed explicitly.

    Values: ``None`` and ``""`` → empty cell; ``str`` → text (never a formula);
    ``date`` → a real date; ``datetime`` → its Tallinn day as a real date;
    ``int``/``float``/``Decimal`` → a number; ``bool`` → «jah»/«ei».
    """

    def __init__(self, workbook: Workbook, title: str, columns: Sequence[Column]) -> None:
        self.sheet: WriteOnlyWorksheet = workbook.create_sheet(sheet_title(title))
        self.columns = list(columns)
        for index, column in enumerate(self.columns, start=1):
            self.sheet.column_dimensions[get_column_letter(index)].width = column.width
        self.sheet.freeze_panes = "A2"
        if self.columns:
            self.sheet.auto_filter.ref = f"A1:{get_column_letter(len(self.columns))}1"
        header = []
        for column in self.columns:
            cell = WriteOnlyCell(self.sheet)
            _set_text(cell, column.heading)
            cell.font = _HEADER_FONT
            header.append(cell)
        self.sheet.append(header)
        self.rows = 0

    def append(self, values: Sequence[Any]) -> None:
        cells = []
        for index, value in enumerate(values):
            wrap = index < len(self.columns) and self.columns[index].wrap
            cells.append(typed_cell(self.sheet, value, wrap=wrap))
        self.sheet.append(cells)
        self.rows += 1

    def finish(self) -> None:
        """Stretch the autofilter over the rows written (the header alone has none)."""
        if self.columns:
            last = get_column_letter(len(self.columns))
            self.sheet.auto_filter.ref = f"A1:{last}{self.rows + 1}"


def _set_text(cell: Any, text: str) -> None:
    text = text[:CELL_LIMIT] if len(text) > CELL_LIMIT else text
    cell.value = text
    # Set after the value: openpyxl infers "f" for a leading "=", and this
    # overrides it. The quote prefix makes Excel keep it as text on edit too.
    cell.data_type = "s"
    if looks_like_formula(text):
        cell.style = "Normal"
        cell.quotePrefix = True


def typed_cell(sheet: WriteOnlyWorksheet, value: Any, *, wrap: bool = False) -> WriteOnlyCell:
    cell = WriteOnlyCell(sheet)
    if value is None or value == "":
        pass
    elif isinstance(value, bool):
        _set_text(cell, "jah" if value else "ei")
    elif isinstance(value, datetime):
        cell.value = timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
        cell.number_format = DATE_FORMAT
    elif isinstance(value, date):
        cell.value = value
        cell.number_format = DATE_FORMAT
    elif isinstance(value, int | float | Decimal):
        cell.value = value
    else:
        _set_text(cell, str(value))
    cell.alignment = _WRAP if wrap else _TOP
    return cell


def new_workbook() -> Workbook:
    """A constant-memory workbook with no default sheet."""
    return Workbook(write_only=True)


def info_sheet(workbook: Workbook, title: str, lines: Iterable[str]) -> None:
    """A one-column sheet of explanatory text (scope, limitations, generated at)."""
    writer = SheetWriter(workbook, title, [Column(title, width=110, wrap=True)])
    for line in lines:
        writer.append([line])


def workbook_bytes(workbook: Workbook) -> bytes:
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def workbook_response(workbook: Workbook, filename: str) -> HttpResponse:
    """The finished workbook as a private download."""
    response = HttpResponse(workbook_bytes(workbook), content_type=XLSX_CONTENT_TYPE)
    response["Content-Disposition"] = content_disposition("attachment", filename)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
