"""What one item of «Menetluse kulg» shows in its popover, and what it shows on the rail (UX-001).

The rail item itself is compact: its name, its date at the precision it was
recorded, one line of description and its acts. Everything else — the whole
description, the notes, whose fact it is and where it is published — is in a
small popover on the item. This module reads that off the `RailStep` the page
already built (`legal_process.matter_rail`) and the records it already carries:
it reads nothing from the database, so the page pays no query per item.

**One fact, one source.** A `Jõustumine` phase can carry several canonical
commencements — «põhiosa 1.1.2027», «osad sätted 1.7.2028» — each with its own
description, its own `ametlik allikas` and its own provenance. The popover keeps
them as separate facts, each ending in its own «Allikas», so a source is never
read as belonging to a neighbour's date.

**«Allikas» is what is stored, and nothing more** (coordinator decision N-A1):

- a record with an `http(s)` source address links to it, showing its host;
- a record that came from Lovable says «Lovable» — and that it is changed there;
- otherwise the colleague who recorded it, or «teadmata».

No address is ever made up, and a record with no address is plain text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

#: Said in the popover of a fact Lovable owns, before its «Allikas».
LOVABLE_OWNED_NOTE = (
    "Lovable'i kirje: muuda või eemalda see Lovable'is — öine sünkroonimine toob muudatuse siia."
)


@dataclass(frozen=True)
class TipSource:
    """«Allikas: …» — a label, and an address only where one is stored."""

    label: str
    url: str = ""
    host: str = ""


@dataclass(frozen=True)
class TipFact:
    """One fact in a popover: its date, its words, its notes, then its source."""

    date: str = ""
    description: str = ""
    notes: tuple[str, ...] = ()
    owned_by_lovable: bool = False
    source: TipSource | None = None


@dataclass(frozen=True)
class RailTip:
    """The visible line under an item's name, and the popover behind it.

    ``lines`` are what the rail prints, one line each, cut with an ellipsis by
    the stylesheet and never in the text: for a phase carrying several facts,
    one per fact — «1.1.2027 · põhiosa», date first — so separate commencements
    stay separate and a narrow column never cuts a date. ``facts`` is the
    popover. An item whose popover would only repeat its own visible lines has
    no ``facts`` and so no popover.
    """

    lines: tuple[str, ...] = ()
    facts: tuple[TipFact, ...] = field(default=())

    @property
    def has_popover(self) -> bool:
        return bool(self.facts)


def _safe_url(value: str) -> tuple[str, str]:
    """``(url, host)`` for an http(s) address, else ``("", "")``."""
    text = (value or "").strip()
    if not text:
        return "", ""
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return "", ""
    host = parts.hostname or parts.netloc
    return text, host.removeprefix("www.")


def _source(record: Any, lovable_ids: frozenset[str]) -> tuple[TipSource | None, bool]:
    """The record's own source, and whether Lovable owns it."""
    if record is None:
        return None, False
    owned = str(getattr(record, "pk", "")) in lovable_ids
    url, host = _safe_url(getattr(record, "source_url", ""))
    if owned:
        return TipSource(label="Lovable", url=url, host=host), True
    author = getattr(record, "created_by", None)
    name = getattr(author, "display_name", "") if author is not None else ""
    return TipSource(label=name or "teadmata", url=url, host=host), False


def _record_text(record: Any) -> tuple[str, tuple[str, ...]]:
    """A record's description and its notes, as stored."""
    description = (
        getattr(record, "description", "")
        or getattr(record, "detail", "")
        or getattr(record, "note", "")
    )
    note = getattr(record, "note", "")
    notes = (note,) if note and note != description else ()
    return description.strip(), tuple(text.strip() for text in notes if text.strip())


def tip_for(step: Any, lovable_ids: frozenset[str] | None = None) -> RailTip:
    """What `step` prints under its name and holds in its popover."""
    owned_ids = frozenset(lovable_ids or ())

    # A phase carrying canonical facts: one visible line per fact, and one
    # popover fact per line, each with the record it came from.
    if step.notes:
        records = tuple(step.note_records) + (None,) * (len(step.notes) - len(step.note_records))
        facts = []
        fact_lines: list[str] = []
        for line, record in zip(step.notes, records, strict=False):
            if record is None:
                facts.append(TipFact(description=line))
                fact_lines.append(line)
                continue
            description, notes = _record_text(record)
            source, owned = _source(record, owned_ids)
            display = getattr(record, "display_date", "") or ""
            # The date first on the rail's one line, so a narrow column's
            # ellipsis cuts the description and never the date (UX-001).
            if isinstance(display, str) and display and description:
                fact_lines.append(f"{display} · {description}")
            else:
                fact_lines.append(line)
            facts.append(
                TipFact(
                    date=display if isinstance(display, str) else "",
                    description=description or line,
                    notes=notes,
                    owned_by_lovable=owned,
                    source=source,
                )
            )
        return RailTip(lines=tuple(fact_lines), facts=tuple(facts))

    record = step.record
    if record is not None:
        description, notes = _record_text(record)
        source, owned = _source(record, owned_ids)
        if step.is_event and getattr(record, "title", "") == description:
            description = ""
        lines = tuple(line for line in (step.display_date, description) if line)
        fact = TipFact(
            date=step.display_date,
            description=description,
            notes=notes,
            owned_by_lovable=owned,
            source=source,
        )
        return RailTip(lines=lines, facts=(fact,))

    # No record: a phase dated on the roadmap, or a dated point such as
    # «Arvamuse tähtaeg» or «Lõpetatud» whose `detail` is its one sentence.
    lines = tuple(line for line in (step.display_date, step.detail) if line)
    detail_facts: tuple[TipFact, ...] = (
        (TipFact(date=step.display_date, description=step.detail),) if step.detail else ()
    )
    return RailTip(lines=lines, facts=detail_facts)


__all__ = ["LOVABLE_OWNED_NOTE", "RailTip", "TipFact", "TipSource", "tip_for"]
