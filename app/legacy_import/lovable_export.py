"""Uuendused → Lovable as an Excel workbook (docs/adr/0201 §6).

Writes only what the read service (`app.legacy_import.lovable_observability`)
already answered for this reader: the view collects the pages, this module
lays them out. Nothing here reads a table, compares a value or decides who may
see what — so a withheld value stays withheld and a Teema the reader may not
open is never in the file (the view counts it on the information sheet).

Every cell goes through `app.core.xlsx_safety`, so a source value that starts
with ``=`` is text and never a formula. Moments are written as Tallinn
``dd.mm.yyyy HH:MM`` text, because a reading's time of day matters and
`SheetWriter` would keep only its day.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from typing import Any

from django.utils import timezone
from openpyxl import Workbook

from app.core.xlsx_safety import Column, SheetWriter, info_sheet, new_workbook
from app.legacy_import.lovable_observation_models import MappingStatus, ObservationScope
from app.legacy_import.lovable_sync_labels import LOVABLE_OBSERVED_KINDS

#: The most rows one export carries; the information sheet says when it stopped.
MAX_ROWS = 5000
CUT_NOTE = f"Väljavõte on piiratud {MAX_ROWS} reaga; kitsenda filtrit, et näha ülejäänut."

KIND_LABELS = dict(LOVABLE_OBSERVED_KINDS)
MAPPING_LABELS = {value: str(label) for value, label in MappingStatus.choices}
POPULATION_LABELS = {value: str(label) for value, label in ObservationScope.choices}

INFO_TITLE = "Teave"


def moment(value: dt.datetime | None) -> str:
    """A stored moment as Tallinn ``dd.mm.yyyy HH:MM`` text, or ``""``."""
    if value is None:
        return ""
    return timezone.localtime(value).strftime("%d.%m.%Y %H:%M")


def filename(view: str, today: dt.date) -> str:
    return f"lovable-{view}-{today:%Y-%m-%d}.xlsx"


def _field_cells(field: Any) -> list[Any]:
    if field is None:
        return ["", "", "", "", ""]
    if field.withheld:
        return [field.label, "", "", True, False]
    return [field.label, field.before, field.after, False, field.truncated]


TOPIC_COLUMNS = [
    Column("Teema", 14),
    Column("Pealkiri", 40, wrap=True),
    Column("Muutuse liik", 16),
    Column("Seos Juristidega", 24),
    Column("Kokkuvõte", 50, wrap=True),
    Column("Väli", 24),
    Column("Enne", 40, wrap=True),
    Column("Pärast", 40, wrap=True),
    Column("Väärtus peidetud", 10),
    Column("Lühendatud", 10),
]


def _topic_head(topic: Any) -> list[Any]:
    reference = topic.reference
    if topic.previous_reference:
        reference = f"{topic.previous_reference} → {topic.reference}"
    return [
        reference,
        topic.title,
        KIND_LABELS.get(topic.kind, topic.kind),
        MAPPING_LABELS.get(topic.mapping, topic.mapping),
        topic.summary,
    ]


class _Rows:
    """Rows collected up to `MAX_ROWS`, so the information sheet — written
    first — can say whether the file was cut short."""

    def __init__(self) -> None:
        self.rows: list[list[Any]] = []
        self.cut = False

    def add(self, row: list[Any]) -> bool:
        if len(self.rows) >= MAX_ROWS:
            self.cut = True
            return False
        self.rows.append(row)
        return True


def _write(
    title: str, columns: Sequence[Column], collected: _Rows, info: Sequence[str]
) -> Workbook:
    workbook = new_workbook()
    info_sheet(workbook, INFO_TITLE, [*info, *([CUT_NOTE] if collected.cut else [])])
    writer = SheetWriter(workbook, title, columns)
    for row in collected.rows:
        writer.append(row)
    writer.finish()
    return workbook


def nights_workbook(readings: Iterable[Any], *, info: Sequence[str]) -> Workbook:
    """One row per changed field per topic per reading; a reading with no
    listed topic gets one row of its own, so its message is not lost."""
    collected = _Rows()
    for reading in readings:
        head = [
            moment(reading.taken_at),
            moment(reading.baseline_taken_at),
            reading.status_label,
            reading.message,
        ]
        rows = [
            head
            + _topic_head(topic)
            + _field_cells(field)
            + ([field.result_label, field.reason] if field is not None else ["", ""])
            for topic in reading.topics
            for field in (topic.fields or [None])
        ] or [head]
        if not all(collected.add(row) for row in rows):
            break
    return _write(
        "Öised võrdlused",
        [
            Column("Lugemine", 17),
            Column("Võrreldud lugemisega", 17),
            Column("Seis", 16),
            Column("Teade", 40, wrap=True),
            *TOPIC_COLUMNS,
            Column("Tulemus Juristides", 22),
            Column("Põhjus", 40, wrap=True),
        ],
        collected,
        info,
    )


def observed_workbook(topics: Iterable[Any], *, info: Sequence[str]) -> Workbook:
    """One row per field name per observed topic change."""
    collected = _Rows()
    for topic in topics:
        population = ", ".join(POPULATION_LABELS.get(p, p) for p in topic.population)
        rows = [
            _topic_head(topic) + _field_cells(field) + [population]
            for field in (topic.fields or [None])
        ]
        if not all(collected.add(row) for row in rows):
            break
    return _write(
        "Vaadeldud muudatused",
        [*TOPIC_COLUMNS, Column("Hulk", 30, wrap=True)],
        collected,
        info,
    )


def queue_workbook(items: Iterable[Any], *, info: Sequence[str]) -> Workbook:
    """One row per pending pilot field."""
    collected = _Rows()
    for item in items:
        if not collected.add(
            [
                moment(item.first_observed_at),
                moment(item.last_observed_at),
                item.age_days,
                item.nights_pending,
                item.reference,
                item.title,
                item.label,
                item.last_applied,
                item.juristid_now,
                item.lovable_now,
                item.outcome_label,
                item.explanation,
                item.suggested_action,
            ]
        ):
            break
    return _write(
        "Ülevaatust ootavad",
        [
            Column("Esmakordselt nähtud", 17),
            Column("Viimati nähtud", 17),
            Column("Vanus (päeva)", 10),
            Column("Öid ootel", 10),
            Column("Teema", 14),
            Column("Pealkiri", 40, wrap=True),
            Column("Väli", 24),
            Column("Viimati rakendatud", 36, wrap=True),
            Column("Juristides praegu", 36, wrap=True),
            Column("Lovable'is praegu", 36, wrap=True),
            Column("Tulemus", 22),
            Column("Selgitus", 40, wrap=True),
            Column("Soovitatud samm", 50, wrap=True),
        ],
        collected,
        info,
    )
