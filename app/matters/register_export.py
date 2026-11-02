"""Teemad as Excel: the table as shown, and every supported field (owner's brief of 2026-11-02).

**«Laadi Excel tabelis kuvatud infoga»** (:func:`table_workbook`) writes every row
the register's current address selects — not just the page on screen — with
exactly the columns «Vali veerud» shows, in the table's order and sort. It reads
the selection pipeline the table reads (`register_selection`, Contract A) and the
catalogue the headings read (`register_columns`, Contract B). Only the *applied*
address counts: a box ticked in the chooser or the filter panel and not yet
submitted is not in the URL and so not in the file.

**«Laadi Excel kõikide andmetega»** (:func:`full_workbook`) ignores the table's
narrowing on purpose. It writes every Teema this reader may see — open, closed
and archive, real data only (test records are left out, as every report leaves
them out) — with every catalogue column plus the record's identity fields, one
worksheet per canonical reporting year and «Teadmata» for the rest. A
reporting year is a register year only for `REGISTER_YEAR_ORIGINS`; the
creation date is never used as one (`selectors.register_year_q`).

Both write through `app.core.xlsx_safety` (typed cells, formula-inert text,
private response) and both read rows in chunks with a bounded number of
statements per chunk, so the cost grows with the rows and not with rows ×
columns. Neither copies anything the reader may not see: the population starts
from `Matter.objects.visible_to`, and every child value comes from a
`visible_to` read in :func:`register_columns.load_facts`.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from datetime import date
from typing import Any

from django.db.models import F
from django.utils import timezone
from openpyxl import Workbook

from app.core.dates import format_estonian_date
from app.core.xlsx_safety import Column as SheetColumn
from app.core.xlsx_safety import SheetWriter, info_sheet, new_workbook
from app.matters import register_columns, register_selection, selectors
from app.matters.enums import REGISTER_YEAR_ORIGINS, MatterOrigin, RecordMode
from app.matters.models import Matter

#: Rows read and written per round. Each chunk costs the same fixed number of
#: statements, so memory stays flat however large the register is.
CHUNK = 400

UNKNOWN_YEAR_SHEET = "Teadmata"
INFO_SHEET = "Teave"


def _base(user: Any) -> Any:
    """The table's own annotated population, plus what the extra columns read."""
    return selectors.matter_list_queryset(user).prefetch_related(
        "legal_instruments", "source_organisations"
    )


def _chunks(ids: Sequence[Any]) -> Iterator[Sequence[Any]]:
    for start in range(0, len(ids), CHUNK):
        yield ids[start : start + CHUNK]


def _rows_in_order(user: Any, ids: Sequence[Any]) -> Iterator[list[Matter]]:
    """The Matters for ``ids``, chunk by chunk, in exactly the order given."""
    for chunk in _chunks(ids):
        loaded = {matter.pk: matter for matter in _base(user).filter(pk__in=chunk)}
        yield [loaded[pk] for pk in chunk if pk in loaded]


def _write(
    writer: SheetWriter,
    user: Any,
    ids: Sequence[Any],
    keys: Sequence[str],
    cells: Sequence[register_columns.Cell],
    *,
    leading: Sequence[Any] = (),
    today: date,
) -> None:
    columns = register_columns.columns_for(keys)
    for rows in _rows_in_order(user, ids):
        facts = register_columns.load_facts(user, rows, columns, today=today)
        for matter in rows:
            prefix = [getter(matter) for getter in leading]
            writer.append([*prefix, *(cell.value(matter, facts) for cell in cells)])
    writer.finish()


def _sheet_columns(cells: Iterable[register_columns.Cell]) -> list[SheetColumn]:
    return [SheetColumn(cell.heading, cell.width, cell.wrap) for cell in cells]


# ---------------------------------------------------------------------------
# Export 1 — the table as shown
# ---------------------------------------------------------------------------


def table_selection(user: Any, params: Any) -> tuple[Any, tuple[str, ...], str]:
    """The ordered rows, the columns and any sort notice — exactly as the table decides them."""
    import dataclasses

    keys = register_columns.chosen_keys(params)
    selection = register_selection.selection_from(user, params)
    effective, notice = register_columns.effective_sort(selection.sort, keys)
    if effective != selection.sort:
        selection = dataclasses.replace(selection, sort=effective)
    return selection.rows(selectors.matter_list_queryset(user)), keys, notice


def table_workbook(
    user: Any, params: Any, *, conditions: Sequence[str], now: Any = None
) -> tuple[Workbook, int]:
    """«Laadi Excel tabelis kuvatud infoga»: every selected row, the shown columns."""
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    rows, keys, notice = table_selection(user, params)
    ids = list(rows.values_list("pk", flat=True))
    cells = register_columns.xlsx_cells(keys)
    workbook = new_workbook()
    info_sheet(
        workbook,
        INFO_SHEET,
        [
            "Juristid · Teemad — tabelis kuvatud info",
            f"Koostatud {format_estonian_date(today)} {timezone.localtime(now):%H:%M}, "
            f"koostaja {getattr(user, 'display_name', '')}.",
            f"Ridu: {len(ids)} — kõik praegusele otsingule ja filtritele vastavad teemad, "
            "mitte ainult ekraanil olev lehekülg.",
            "Tingimused: " + ("; ".join(conditions) if conditions else "filtreid ei ole."),
            "Veerud: " + ", ".join(column.heading for column in register_columns.columns_for(keys)),
            *([notice] if notice else []),
            "Kuupäev on kuupäevalahtris ainult siis, kui see on täpne päev; kuu, kvartal "
            "või aasta on kirjas sõnadega. Tühi lahter tähendab, et väärtust ei ole kirjas.",
            "Ainult teemad ja andmed, mida sul on õigus näha.",
        ],
    )
    writer = SheetWriter(workbook, "Teemad", _sheet_columns(cells))
    _write(writer, user, ids, keys, cells, today=today)
    return workbook, len(ids)


# ---------------------------------------------------------------------------
# Export 2 — every supported field
# ---------------------------------------------------------------------------

#: The record's identity, before the catalogue columns.
_IDENTITY = (
    register_columns.Cell("Teema ID", lambda m, f: str(m.pk), 38),
    register_columns.Cell(
        "Päritolu", lambda m, f: dict(MatterOrigin.choices).get(m.origin, m.origin), 18
    ),
    register_columns.Cell(
        "Kirje liik", lambda m, f: dict(RecordMode.choices).get(m.record_mode, m.record_mode), 12
    ),
    register_columns.Cell("Loodud", lambda m, f: timezone.localtime(m.created_at).date(), 12),
    register_columns.Cell(
        "Suletud", lambda m, f: timezone.localtime(m.closed_at).date() if m.closed_at else None, 12
    ),
)

ALL_KEYS: tuple[str, ...] = tuple(column.key for column in register_columns.CATALOGUE)


def full_population(user: Any) -> Any:
    """Every Teema this reader may see, real data only, without the table's narrowing."""
    return Matter.objects.visible_to(user).real_data()


def _year_groups(user: Any) -> tuple[dict[int, list[Any]], list[Any]]:
    """Ids per canonical reporting year, newest first, and the unknown-year ids."""
    years: dict[int, list[Any]] = {}
    unknown: list[Any] = []
    for pk, year, origin in (
        full_population(user)
        .order_by(
            F("reference_year").asc(nulls_last=True),
            F("reference_number").asc(nulls_last=True),
            "title",
            "pk",
        )
        .values_list("pk", "reporting_year", "origin")
    ):
        if year and origin in REGISTER_YEAR_ORIGINS:
            years.setdefault(int(year), []).append(pk)
        else:
            unknown.append(pk)
    return dict(sorted(years.items(), reverse=True)), unknown


def full_workbook(user: Any, *, now: Any = None) -> tuple[Workbook, int]:
    """«Laadi Excel kõikide andmetega»: one sheet per reporting year, then «Teadmata»."""
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    years, unknown = _year_groups(user)
    total = sum(len(ids) for ids in years.values()) + len(unknown)
    cells = [*_IDENTITY, *register_columns.xlsx_cells(ALL_KEYS)]
    workbook = new_workbook()
    info_sheet(
        workbook,
        INFO_SHEET,
        [
            "Juristid · Teemad — kõik andmed",
            f"Koostatud {format_estonian_date(today)} {timezone.localtime(now):%H:%M}, "
            f"koostaja {getattr(user, 'display_name', '')}.",
            f"Teemasid kokku: {total}. Ulatus: kõik teemad, mida sul on õigus näha — avatud, "
            "suletud ja arhiivikirjed. Teemade tabeli filtrid ja otsing ei kehti. "
            "Testkirjed ei ole kaasas.",
            "Üks leht aruandlusaasta kohta. Aruandlusaasta on registri aasta; teema, millel "
            f"seda ei ole, on lehel «{UNKNOWN_YEAR_SHEET}». Loomise kuupäeva aastana ei kasutata.",
            *(f"{year}: {len(ids)} teemat" for year, ids in years.items()),
            f"{UNKNOWN_YEAR_SHEET}: {len(unknown)} teemat",
            "Ei sisalda dokumentide sisu, isiklikke märkmeid ega auditilogi. Kuupäev on "
            "kuupäevalahtris ainult siis, kui see on täpne päev.",
        ],
    )
    for year, ids in years.items():
        _write(
            SheetWriter(workbook, str(year), _sheet_columns(cells)),
            user,
            ids,
            ALL_KEYS,
            register_columns.xlsx_cells(ALL_KEYS),
            leading=[_identity_getter(cell) for cell in _IDENTITY],
            today=today,
        )
    _write(
        SheetWriter(workbook, UNKNOWN_YEAR_SHEET, _sheet_columns(cells)),
        user,
        unknown,
        ALL_KEYS,
        register_columns.xlsx_cells(ALL_KEYS),
        leading=[_identity_getter(cell) for cell in _IDENTITY],
        today=today,
    )
    return workbook, total


def _identity_getter(cell: register_columns.Cell) -> Any:
    return lambda matter: cell.value(matter, None)  # type: ignore[arg-type]


def table_filename(today: date) -> str:
    return f"Teemad_tabel_{today.isoformat()}.xlsx"


def full_filename(today: date) -> str:
    return f"Teemad_koik_andmed_{today.isoformat()}.xlsx"


__all__ = [
    "full_filename",
    "full_workbook",
    "table_filename",
    "table_selection",
    "table_workbook",
]
