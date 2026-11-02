"""The DOCX writer for «Majanduspoliitika ülevaade».

It writes exactly ``ComposedReport.table_rows()`` — the rows the preview
prints — so the page and the file cannot disagree about order, text or which
topics are in (docs/adr/0199 §4).

What the document is, deliberately:

* **real, editable Word content** — a heading, the period, one table. No
  image of a table, no fields, no macros, no template from outside the
  package: python-docx's own default document, styled here;
* **A4 portrait**, 2 cm margins, the table's four columns sized for the
  17 cm text width («Teema» | «Kuupäev» | «Kellele» | «Selgitus»);
* **one block per topic**: a row per event, the «Teema» cell merged
  vertically across them, so each event's date, recipient and explanation
  stay on one row. A topic with nothing in the period is one row with the
  title alone — no date is invented to fill it;
* **the header row repeats on every page** (``w:tblHeader``) and no row is
  forbidden to split, so a long explanation flows onto the next page rather
  than pushing a whole row ahead of it;
* **Estonian**: ``et-EE`` on the document defaults (spelling and
  hyphenation) and in the core properties.
"""

from __future__ import annotations

import io

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from docx.table import _Cell

from app.core.xlsx_safety import xml_safe
from app.reporting.report_builder.draft import ComposedReport

TITLE = "Majanduspoliitika ülevaade"
HEADERS = ("Teema", "Kuupäev", "Kellele", "Selgitus")
#: Column widths in centimetres; they add up to the 17 cm text width.
WIDTHS_CM = (4.2, 2.6, 3.6, 6.6)
LANGUAGE = "et-EE"
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


def _write_lines(cell: _Cell, lines: list[tuple[str, str]]) -> None:
    """Paragraphs into ``cell``: ``(text, style)`` where style is ``""`` or ``"meta"``."""
    first = True
    for text, style in lines:
        for line in text.split("\n") or [""]:
            paragraph = cell.paragraphs[0] if first else cell.add_paragraph()
            first = False
            paragraph.paragraph_format.space_after = Pt(2)
            run = paragraph.add_run(xml_safe(line))
            if style == "meta":
                run.italic = True
                run.font.size = Pt(8)
            elif style == "bold":
                run.bold = True


def _size(cell: _Cell, width: float) -> None:
    cell.width = Cm(width)


def render(report: ComposedReport) -> bytes:
    """The report as DOCX bytes."""
    document = Document()
    _set_language(document)
    document.core_properties.title = f"{TITLE} {report.period_label}"
    document.core_properties.author = ""
    document.core_properties.comments = ""

    section = document.sections[0]
    section.orientation = WD_ORIENT.PORTRAIT
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Cm(2.0))

    normal = document.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10)

    document.add_heading(TITLE, level=1)
    document.add_paragraph(report.period_label)

    rows = report.table_rows()
    table = document.add_table(rows=1, cols=len(HEADERS))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    # The grid is what LibreOffice and Word lay a fixed table out by; the cell
    # widths below alone leave python-docx's equal split in place.
    for column, width in zip(table.columns, WIDTHS_CM, strict=True):
        column.width = Cm(width)

    header = table.rows[0]
    _repeat_as_header(header)
    for index, text in enumerate(HEADERS):
        cell = header.cells[index]
        _size(cell, WIDTHS_CM[index])
        _write_lines(cell, [(text, "bold")])

    topic_start: _Cell | None = None
    for row in rows:
        cells = table.add_row().cells
        for index, cell in enumerate(cells):
            _size(cell, WIDTHS_CM[index])
        if row.topic_span:
            _write_lines(cells[0], [(row.topic, "")])
            topic_start = cells[0]
        elif topic_start is not None:
            # Merged into the topic cell above: Word shows one cell spanning
            # the topic's events (w:vMerge), and python-docx keeps the text.
            topic_start = topic_start.merge(cells[0])
        date_lines = [(row.date_label, "")] if row.date_label else []
        if row.kind_label:
            date_lines.append((row.kind_label, "meta"))
        if row.provenance:
            date_lines.append((row.provenance, "meta"))
        _write_lines(cells[1], date_lines)
        _write_lines(cells[2], [(row.recipients, "")] if row.recipients else [])
        _write_lines(cells[3], [(row.text, "")] if row.text else [])

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
