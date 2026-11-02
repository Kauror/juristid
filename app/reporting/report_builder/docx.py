"""The DOCX writer for «Majanduspoliitika ülevaade».

It writes exactly ``ComposedReport.table_rows()`` — the rows the preview
prints — so the page and the file cannot disagree about order, text or which
topics are in (docs/adr/0199 §4).

What the document is, deliberately: **the department's report as it has
always been sent to the board** —

* the fixed opening from the yearly action plan (`plan_text`): two bold
  lines, «Tegevused ja eesmärgid» and the two-column plan table;
* **«Täitmine: 12.08.2026 – 02.09.2026»** in bold, then one table
  «Teema» | «Kuupäev» | «Kellele» | «Selgitus», a row per event, the topic
  written on each row and the day as «12.08». The preview's kind and source
  labels are not written; a topic with nothing in the period is one row with
  the title alone — no date is invented to fill it;
* the day it was made and the signers, in bold, under the table;
* **real, editable Word content** — no image of a table, no fields, no
  macros, no template from outside the package; A4 portrait, 2.5 cm margins,
  Times New Roman 12;
* **the header row repeats on every page** (``w:tblHeader``) and no row is
  forbidden to split, so a long explanation flows onto the next page rather
  than pushing a whole row ahead of it;
* **Estonian**: ``et-EE`` on the document defaults (spelling and
  hyphenation) and in the core properties.
"""

from __future__ import annotations

import io
from datetime import date
from typing import Any

from django.utils import timezone
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from docx.table import Table, _Cell

from app.core.xlsx_safety import xml_safe
from app.reporting.report_builder import plan_text
from app.reporting.report_builder.draft import ComposedReport

TITLE = "Majanduspoliitika ülevaade"
HEADERS = ("Teema", "Kuupäev", "Kellele", "Selgitus")
#: Column widths in centimetres, as in the department's own reports: wider
#: than the 16 cm text width, the centred table reaching into the margins, so
#: «Kuupäev» and «Kommunikatsiooniministeerium» fit on one line.
WIDTHS_CM = (4.2, 2.1, 6.0, 6.8)
PLAN_WIDTHS_CM = (5.7, 13.1)
LANGUAGE = "et-EE"
FONT = "Times New Roman"
CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _set_language(document: Document) -> None:  # type: ignore[valid-type]
    """``et-EE`` on the document defaults, so every run inherits it."""
    styles = document.styles.element  # type: ignore[attr-defined]
    defaults = styles.find(qn("w:docDefaults"))
    if defaults is None:
        defaults = OxmlElement("w:docDefaults")
        styles.insert(0, defaults)
    rpr_default = defaults.find(qn("w:rPrDefault"))
    if rpr_default is None:
        rpr_default = OxmlElement("w:rPrDefault")
        defaults.insert(0, rpr_default)
    rpr = rpr_default.find(qn("w:rPr"))
    if rpr is None:
        rpr = OxmlElement("w:rPr")
        rpr_default.append(rpr)
    lang = rpr.find(qn("w:lang"))
    if lang is None:
        lang = OxmlElement("w:lang")
        rpr.append(lang)
    for attribute in ("w:val", "w:eastAsia", "w:bidi"):
        lang.set(qn(attribute), LANGUAGE)
    document.core_properties.language = LANGUAGE  # type: ignore[attr-defined]


def _repeat_as_header(row: object) -> None:
    tr_pr = row._tr.get_or_add_trPr()  # type: ignore[attr-defined]
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    tr_pr.append(header)


def _write_lines(cell: _Cell, text: str, *, bold: bool = False) -> None:
    """``text`` into ``cell``, a paragraph per line."""
    for index, line in enumerate(text.split("\n")):
        paragraph = cell.paragraphs[0] if index == 0 else cell.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(0)
        run = paragraph.add_run(xml_safe(line))
        run.bold = bold or None


def _bold_line(document: Document, text: str) -> None:  # type: ignore[valid-type]
    document.add_paragraph().add_run(xml_safe(text)).bold = True  # type: ignore[attr-defined]


def _table(document: Any, widths: tuple[float, ...]) -> Table:
    table = document.add_table(rows=0, cols=len(widths))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    # The grid is what LibreOffice and Word lay a fixed table out by; the cell
    # widths alone leave python-docx's equal split in place.
    for column, width in zip(table.columns, widths, strict=True):
        column.width = Cm(width)
    return table


def _add_row(table: Table, widths: tuple[float, ...]) -> list[_Cell]:
    cells = table.add_row().cells
    for cell, width in zip(cells, widths, strict=True):
        cell.width = Cm(width)
    return list(cells)


def _plan(document: Document) -> None:  # type: ignore[valid-type]
    """The fixed opening from the department's action plan."""
    for heading in plan_text.HEADINGS:
        _bold_line(document, heading)
    document.add_paragraph(plan_text.INTRODUCTION)  # type: ignore[attr-defined]
    table = _table(document, PLAN_WIDTHS_CM)
    first, second = _add_row(table, PLAN_WIDTHS_CM)
    _write_lines(first.merge(second), plan_text.TABLE_TITLE, bold=True)
    for label, text in plan_text.ROWS:
        label_cell, text_cell = _add_row(table, PLAN_WIDTHS_CM)
        _write_lines(label_cell, label)
        _write_lines(text_cell, text)
    document.add_paragraph()  # type: ignore[attr-defined]


def render(report: ComposedReport, *, today: date | None = None) -> bytes:
    """The report as DOCX bytes; ``today`` is the day printed above the signers."""
    today = today or timezone.localdate()
    document = Document()
    _set_language(document)
    document.core_properties.title = f"{TITLE} {report.period_label}"
    document.core_properties.author = ""
    document.core_properties.comments = ""

    section = document.sections[0]
    section.orientation = WD_ORIENT.PORTRAIT
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Cm(2.5))

    normal = document.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(12)

    _plan(document)
    _bold_line(document, report.execution_line)

    table = _table(document, WIDTHS_CM)
    header = _add_row(table, WIDTHS_CM)
    _repeat_as_header(table.rows[0])
    for cell, text in zip(header, HEADERS, strict=True):
        _write_lines(cell, text, bold=True)
    for row in report.table_rows():
        cells = _add_row(table, WIDTHS_CM)
        texts = (row.topic, row.date_label, row.recipients, row.text)
        for cell, text in zip(cells, texts, strict=True):
            if text:
                _write_lines(cell, text)

    document.add_paragraph()
    _bold_line(document, f"{today:%d.%m.%Y}")
    if report.signers:
        _bold_line(document, report.signers)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
