"""How the three Jälgimine pages divide what they show.

The v2 design replaced one long chronological list per page with named
sections — *Järgmised 30 päeva* and *Hiljem*, *Jõustub varsti* and *Jõustunud*,
one section per year — each showing a few rows with the rest behind
«Näita veel N ▾» (02-EKRAANID §D).

Two properties this module exists to hold.

**The overflow is the same list.** ``rest`` is a slice of ``rows``, not a second
query, so the count in the heading, the rows on screen and the number on the
disclosure are three readings of one answer.

**Nothing here authorizes anything.** The rows arrive already scoped by
``visible_to`` in :mod:`app.intelligence.selectors`; this only decides where a
row is drawn.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

#: How many rows a section shows before the rest go behind the disclosure.
#: Five, which is what the design draws and what fits above the fold beside the
#: strip (02-EKRAANID §D).
SECTION_VISIBLE = 5

#: What «Järgmised 30 päeva» means on Olulised tähtajad, and how far
#: «Jõustub varsti» reaches on Jõustuvad aktid.
NEAR_DAYS = 30
SOON_DAYS = 90


@dataclass(frozen=True)
class Section:
    """One named group of rows, and the part of it that is on screen."""

    key: str
    label: str
    rows: list[Any]
    visible: int = SECTION_VISIBLE

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def preview(self) -> list[Any]:
        return self.rows[: self.visible]

    @property
    def rest(self) -> list[Any]:
        return self.rows[self.visible :]

    @property
    def remaining(self) -> int:
        return len(self.rest)


def _anchor(row: Any) -> date | None:
    """The day a row is filed under: the *first* day of whatever period it names.

    A quarter beginning in October is October's problem even though it runs to
    December, so the near/far split reads the anchor. Whether a period has
    *passed* is a different question and reads ``period_end`` — the two are not
    interchangeable and the selectors keep them apart
    (app/intelligence/selectors.py).
    """
    record = getattr(row, "record", row)
    return getattr(record, "date_value", None)


def split_near_and_later(
    rows: list[Any], today: date, *, days: int = NEAR_DAYS, near_label: str, later_label: str
) -> list[Section]:
    """Two sections: what falls inside the window, and everything after it.

    A row with no date at all is not silently dropped — it goes to the later
    section, which is where a reader looking for "and what else is there" will
    be. Inventing a day for it so it could be sorted into the near window is
    exactly what the date-precision rule forbids (01-EHITUSJUHIS §3.2).
    """
    horizon = today + timedelta(days=days)
    near: list[Any] = []
    later: list[Any] = []
    for row in rows:
        anchor = _anchor(row)
        (near if anchor is not None and anchor <= horizon else later).append(row)
    sections = []
    if near:
        sections.append(Section(key="lahedal", label=near_label, rows=near))
    if later:
        sections.append(Section(key="hiljem", label=later_label, rows=later))
    return sections


def one_section(key: str, label: str, rows: list[Any]) -> list[Section]:
    """A single section, for the views a filter has already narrowed.

    A reader who asked for *Möödunud* has said which window they want; splitting
    that answer again into two windows they did not ask for is the page
    arguing with them.
    """
    return [Section(key=key, label=label, rows=rows)] if rows else []


def by_year(rows: list[Any], *, period_of: Any, unknown_label: str) -> list[Section]:
    """One section per year, newest first, with the undated ones last.

    Töövõidud are read by year — «mis me sel aastal saavutasime» — and a record
    whose period nobody knows is never folded into a year it might not belong
    to (Stage-2G brief 27).
    """
    grouped: dict[int | None, list[Any]] = {}
    for row in rows:
        grouped.setdefault(period_of(row), []).append(row)
    known = sorted((year for year in grouped if year is not None), reverse=True)
    sections = [Section(key=str(year), label=str(year), rows=grouped[year]) for year in known]
    if None in grouped:
        sections.append(Section(key="teadmata", label=unknown_label, rows=grouped[None]))
    return sections
