"""A Statistika panel as an Excel workbook, from its chart spec (UX-005).

Two sheets, through the workbook safety helpers every Statistika export uses
(`app.core.xlsx_safety`): «Andmed» — one row per category, one column per
series, every value a typed number — and «Teave» — the title, the period, the
filters, the notes, who may see what was counted, and when the file was made.

The numbers are the spec's values, which are the page's: nothing is computed
here. An unknown value is a **blank cell**, never 0, and the info sheet says so.
"""

from __future__ import annotations

from datetime import datetime

from openpyxl import Workbook

from app.core import xlsx_safety as xs
from app.reporting import chart_spec as cs

DATA_SHEET = "Andmed"
INFO_SHEET = "Teave"

#: Said on every info sheet: what the file holds and what it does not.
SCOPE = (
    "Arvestatud on ainult teemad, mida allalaadija tohib näha; sama vaade ja samad"
    " filtrid nagu lehel Statistika."
)
BLANK = "Tühi lahter tähendab, et andmed puuduvad (väärtust ei saa arvutada) — see ei ole null."


def _heading(series: cs.Series) -> str:
    return f"{series.name} ({series.unit})" if series.unit else series.name


def columns(spec: cs.ChartSpec) -> list[str]:
    """The data sheet's headings, in order."""
    out = [spec.category_heading or "Kategooria", *(_heading(s) for s in spec.series)]
    if spec.totals:
        out.append("Kokku (arvamust)")
    if any(spec.partial):
        out.append("Aasta on pooleli")
    return out


def rows(spec: cs.ChartSpec) -> list[list[object]]:
    """The data sheet's rows: the spec's own values, ``None`` kept."""
    out = []
    for index, (title, values) in enumerate(spec.rows()):
        row: list[object] = [title, *values]
        if spec.totals:
            row.append(spec.totals[index])
        if any(spec.partial):
            row.append("jah" if spec.partial[index] else None)
        out.append(row)
    return out


def info_lines(spec: cs.ChartSpec, generated: datetime) -> list[str]:
    lines = [
        spec.title,
        f"Periood: {spec.period}",
        *(f"{label}: {value}" for label, value in spec.filters),
        *spec.notes,
        BLANK,
        SCOPE,
        f"Koostatud: {generated:%d.%m.%Y %H:%M} · Juristid, Eesti Kaubandus-Tööstuskoda",
    ]
    if spec.empty:
        lines.insert(1, "Andmed puuduvad: valitud vaates ei ole selle paneeli kohta andmeid.")
    return lines


def workbook(spec: cs.ChartSpec, generated: datetime) -> Workbook:
    book = xs.new_workbook()
    heads = columns(spec)
    writer = xs.SheetWriter(
        book,
        DATA_SHEET,
        [xs.Column(heads[0], width=34, wrap=True), *(xs.Column(h, width=20) for h in heads[1:])],
    )
    for row in rows(spec):
        writer.append(row)
    writer.finish()
    xs.info_sheet(book, INFO_SHEET, info_lines(spec, generated))
    return book
