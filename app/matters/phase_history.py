"""Grouping one Matter's chronology into the phases the procedure actually had.

`Teema käik` used to be one flat list of everything, newest first. A lawyer
opening a three-year file got thirty dates in a column and had to reconstruct the
shape of the proceeding in their head. This groups the same rows — the same
projection, the same canonical records, the same visibility — into the phases the
file **recorded**, so the history reads as `VTK`, then `Kooskõlastusring`, then
`Riigikogus`.

Nothing here is a second history
--------------------------------
This reads the list `app/matters/timeline.py` has already built and assigns each
row a phase. It queries nothing, writes nothing, stores nothing and holds no
record of its own. A second projection of «what happened on this file» would be a
second answer, and the day they disagreed nobody could say which was meant
(docs/adr/0092, Alternatives).

How a row gets its phase
------------------------
In this order, stopping at the first answer that is actually supported:

1. **An explicit association the record holds.** `MatterProceduralDevelopment.
   process_phase`, set by a person who saw it, on a step of the procedure. This
   is the only explicit phase in the product, and everything else is measured
   against it.
2. **A business-dated interval between two such records.** Each dated,
   phase-bearing development opens a phase and the next one closes it. Every row
   whose own business date falls inside is in that phase.
3. **Otherwise, unplaced.** Which is an ordinary answer and not a defect.

And these, explicitly, are evidence of nothing:

* a title, a filename, an organisation's name, a `Menetluse link`'s host;
* an upload time, an import time, a `created_at`, today's date;
* two records having been *entered* on the same day, or in a particular order;
* the file's current `Hetkeseis` — which says where the procedure is now and
  dates nothing, least of all an opinion sent last spring.

Three rules that are easy to get wrong
--------------------------------------
**An interval must be unambiguous, or it does not exist.** The last dated
development leaves an interval running to the present, and that reading holds
only while nothing contradicts it. A current `Hetkeseis` naming a *different*
phase does contradict it: it proves the file left, without saying when.
Everything after that last development is then unplaced until somebody records
the step that moved it — one `+ Märge`, and the rows snap into place.

**A phase occurs more than once.** `Kooskõlastusring` for the VTK and
`Kooskõlastusring` for the bill are two occurrences a year apart, and merging
them because their keys match would put the bill's consultation inside the VTK's.
Everything here is keyed on the **occurrence**, never on the phase.

**Nothing is completed by implication.** A file that recorded `Riigikogus` and
nothing earlier has no earlier sections at all — not empty ones, and certainly
not ticked ones. An absent section is the honest rendering of «Koda was not
there, or nobody wrote it down» (docs/adr/0092 §13).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from app.audit.enums import ChangeEventType
from app.matters.process_phases import UNPLACED_LABEL, ProcessPattern, phase_label

#: The key the unplaced group is addressed by.
#:
#: Not a phase, and deliberately not a member of the phase vocabulary: nothing
#: may ever be *stored* as unplaced, because unplaced is the absence of a stored
#: value rather than one of its possible values.
UNPLACED_KEY = "__unplaced__"

#: The model whose explicit column anchors everything. Compared by name so this
#: module imports no model and can be reasoned about — and tested — on its own.
_ANCHOR_MODEL = "MatterProceduralDevelopment"


@dataclass(frozen=True)
class PhaseOccurrence:
    """One run of one phase on one file, or the unplaced group.

    ``index`` is what a row is grouped by and is unique per Matter per render.
    Two `Kooskõlastusring` occurrences a year apart carry the same ``phase_key``
    and different indexes, which is the whole reason rows group on this object
    rather than on the key.

    ``started_on`` is the business date of the development that opened the phase —
    never a `created_at`, never an audit timestamp and never today. It is ``None``
    on exactly one occurrence: the current phase, when the file says where it is
    and records no dated arrival. That occurrence carries no date rather than
    borrowing one, which is the substitution docs/adr/0092 §4 refuses.
    """

    index: int
    phase_key: str
    label: str
    started_on: date | None = None
    is_current: bool = False

    @property
    def is_unplaced(self) -> bool:
        return self.phase_key == UNPLACED_KEY

    @property
    def anchor(self) -> str:
        """The fragment id this section is addressable by."""
        return f"etapp-{self.index}"


@dataclass(frozen=True)
class PhaseHistory:
    """What the chronology looks like once it is grouped.

    ``current_without_rows`` is the current phase when the file records nothing
    in it — «the bill is in the Riigikogu and nothing has happened there yet». It
    is carried separately because it has no row to hang a heading on, and because
    a section that exists only to say where the file is belongs at the top of the
    history. **It is never filled in with an invented event** (§6).

    ``grouped`` is false when the file supports no phase anywhere, and then the
    chronology renders exactly as it always has: one flat list, no headings and no
    `Etapiga sidumata`. A heading saying «none of this could be placed» above
    every row of every unclassified file would be the application announcing a gap
    nobody can close (docs/adr/0092 §16).
    """

    occurrences: tuple[PhaseOccurrence, ...] = ()
    current_without_rows: PhaseOccurrence | None = None
    grouped: bool = False


def _is_anchor_model(record: Any) -> bool:
    return record is not None and type(record).__name__ == _ANCHOR_MODEL


def _stage_change_only(item: Any) -> bool:
    """Whether this row is a bare `Hetkeseis` edit and nothing else.

    Such a row proves somebody **recorded a value**; its timestamp is the moment
    they typed it, and it says nothing about when an external procedure moved.
    Placing it by that timestamp would date a step of somebody else's proceeding
    to a day in this application's own life — the substitution docs/adr/0092 §4
    refuses — and it is what stops a corrected mistake from leaving a phase behind
    it that the procedure never visited.

    A stage change saved *with* a development is not this: it folds onto that
    development's row and never appears alone (docs/adr/0092 §7).
    """
    if item.record is not None or item.entry is not None:
        return False
    events = item.events or ((item.event,) if item.event is not None else ())
    return bool(events) and all(
        event is not None and event.event_type == ChangeEventType.MATTER_STAGE_CHANGED
        for event in events
    )


def business_day(item: Any, *, local_day: Any) -> date | None:
    """The day this row's fact actually happened, or ``None`` when nobody knows.

    **Not where the row sits.** `engagement_chronology_day` and its siblings fall
    back to `created_at` so an undated record still has a place in a list; that
    fallback places a row and deliberately never describes it, and grouping is a
    description. A `Kaasamine` reading «kuupäev teadmata» must not be quietly
    filed under whichever phase the file happened to be in on the day somebody
    typed it up (docs/adr/0078 §2).

    So every branch reads the record's **own** business column and answers
    ``None`` where it is empty. An approximate period answers with its anchor —
    the first day of the month or quarter, which is what an anchor is for — and
    the row goes on printing *oktoober 2026* wherever it lands (docs/adr/0079 §2).
    """
    record = item.record
    if record is not None:
        for column in ("occurred_on", "stated_on", "published_on"):
            if hasattr(record, column):
                value = getattr(record, column)
                return value if isinstance(value, date) else None
        sent_at = getattr(record, "sent_at", None)
        if sent_at is not None:
            return local_day(sent_at)
        # `Oluline tähtaeg`, `Jõustumine` and `Töövõit` each carry a real period
        # and have no fallback: the projection builds no row for one without a
        # date, so whatever is here is what somebody recorded.
        for column in ("period_date", "date_value"):
            value = getattr(record, column, None)
            if isinstance(value, date):
                return value
        cancelled_at = getattr(record, "cancelled_at", None)
        if cancelled_at is not None:
            return local_day(cancelled_at)
        return None
    if item.entry is not None:
        # `Entry.occurred_at` is documented as *when the work happened, which is
        # not when it was typed up*, and it is `NOT NULL`.
        return local_day(item.entry.occurred_at)
    if _stage_change_only(item):
        return None
    if item.event is not None:
        # What is left is this office's own acts — a file captured, a step set, an
        # opinion withdrawn, the Matter opened or closed. Each happened on the day
        # this application recorded it happening, because this application is
        # where it happened. That is not the forbidden substitution: what §9
        # refuses is dating somebody *else's* legislative step by our clock.
        return local_day(item.event.occurred_at)
    return None


def _runs(
    items: list[Any], *, phase_keys: frozenset[str], local_day: Any
) -> list[tuple[str, date]]:
    """The phases this file can prove it was in, earliest first, as ``(key, day)``.

    Read from the **already-scoped** list, so a development a reader may not see
    opens no phase for them, moves no heading and leaves no gap where one would
    have been. Scoping before grouping rather than filtering afterwards is the
    rule AUTH-003 closed and docs/adr/0092 §7 restates.
    """
    dated: list[tuple[date, Any, str, str]] = []
    for item in items:
        if not _is_anchor_model(item.record):
            continue
        phase = getattr(item.record, "process_phase", "") or ""
        if phase not in phase_keys:
            # Blank, or a key this version of the vocabulary no longer knows.
            # Both mean «this places nothing», and neither is an error.
            continue
        day = business_day(item, local_day=local_day)
        if day is None:
            # A development whose own date is unknown opens no dated interval —
            # there is no day for one to begin on. The row still reads, and still
            # reads under the phase it names.
            continue
        dated.append((day, item.created_at, item.sort_key, phase))
    dated.sort(key=lambda row: (row[0], row[1], row[2]))

    runs: list[tuple[str, date]] = []
    for day, _created, _sort, phase in dated:
        if runs and runs[-1][0] == phase:
            # The same round described twice, not two rounds. A different phase
            # in between is what makes the next one a second occurrence, which is
            # the whole of what «repeated phases» means here.
            continue
        runs.append((phase, day))
    return runs


def build(
    items: list[Any],
    *,
    pattern: ProcessPattern | None,
    stage_key: str,
    phase_keys: frozenset[str],
    local_day: Any,
) -> tuple[list[Any], PhaseHistory]:
    """Group one Matter's chronology, returning the rows in reading order.

    The returned list holds the same rows, re-ordered so each occurrence's rows
    are contiguous and each row knows which occurrence it is in. Nothing is added,
    nothing is dropped and nothing is de-duplicated: a grouping that lost a row
    would be a history that lost a fact.

    Reading order is **newest phase first, newest row first inside it** — the
    order the flat list already had, lifted one level. `Etapiga sidumata` is last,
    because it is the part of the file nobody has placed rather than the part that
    happened first.
    """
    if not items:
        return items, PhaseHistory()

    runs = _runs(items, phase_keys=phase_keys, local_day=local_day)
    current_phase = pattern.phase_for_stage(stage_key) if pattern is not None else ""

    # **Where the last interval stops.** It runs to the present while nothing
    # contradicts it, and a current `Hetkeseis` naming a different phase does:
    # the file demonstrably left, and nothing dated says when. Everything after
    # the last development is then genuinely ambiguous, which §7 of the brief
    # answers with «unplaced» rather than with a guess.
    open_ended = not runs or not current_phase or current_phase == runs[-1][0]

    occurrences = [
        PhaseOccurrence(
            index=index,
            phase_key=phase,
            label=phase_label(phase),
            started_on=day,
            is_current=bool(current_phase) and phase == current_phase and index == len(runs) - 1,
        )
        for index, (phase, day) in enumerate(runs)
    ]

    current_without_rows: PhaseOccurrence | None = None
    if current_phase and (not runs or runs[-1][0] != current_phase):
        # The file says where it is and records no dated arrival. The section
        # exists, says `Praegu`, carries no date and holds no rows — and nothing
        # invents an event to fill it (§6).
        current_without_rows = PhaseOccurrence(
            index=len(occurrences),
            phase_key=current_phase,
            label=phase_label(current_phase),
            started_on=None,
            is_current=True,
        )

    unplaced = PhaseOccurrence(
        index=len(occurrences) + 1,
        phase_key=UNPLACED_KEY,
        label=UNPLACED_LABEL,
    )
    by_index = {occurrence.index: occurrence for occurrence in occurrences}
    if current_without_rows is not None:
        by_index[current_without_rows.index] = current_without_rows

    def occurrence_for(item: Any) -> PhaseOccurrence:
        day = business_day(item, local_day=local_day)
        if _is_anchor_model(item.record):
            # **Rule 1 — the record's own explicit phase.** It places the row even
            # when the row carries no date at all, because somebody said so: an
            # undated development opens no interval and is still perfectly
            # placeable in the one it names.
            phase = getattr(item.record, "process_phase", "") or ""
            if phase in phase_keys:
                for occurrence in reversed(occurrences):
                    if occurrence.phase_key != phase:
                        continue
                    if day is not None and occurrence.started_on is not None:
                        if day >= occurrence.started_on:
                            return occurrence
                        continue
                    return occurrence
                if current_without_rows is not None and current_without_rows.phase_key == phase:
                    return current_without_rows
                return unplaced
        if day is None:
            return unplaced
        # **Rule 2 — the interval.** `[start, next start)`, decided on business
        # dates alone. Two rows sharing a boundary day fall the same way whatever
        # order they were entered in and whatever order they are read in, which is
        # what stops a same-day coincidence from manufacturing membership.
        for index, occurrence in enumerate(occurrences):
            start = occurrence.started_on
            if start is None or day < start:
                continue
            if index + 1 < len(occurrences):
                following = occurrences[index + 1].started_on
                if following is not None and day >= following:
                    continue
                return occurrence
            if open_ended or day == start:
                return occurrence
            # Contested tail: the file left this phase, and nothing says when.
            return unplaced
        return unplaced

    buckets: dict[int, list[Any]] = {}
    for item in items:
        buckets.setdefault(occurrence_for(item).index, []).append(item)

    reading_order: list[PhaseOccurrence] = []
    if current_without_rows is not None:
        reading_order.append(current_without_rows)
    reading_order.extend(reversed(occurrences))
    reading_order.append(unplaced)

    ordered: list[Any] = []
    rendered: list[PhaseOccurrence] = []
    for occurrence in reading_order:
        rows = buckets.get(occurrence.index, [])
        if not rows:
            continue
        rendered.append(occurrence)
        for position, row in enumerate(rows):
            ordered.append(replace(row, phase=occurrence, opens_phase=position == 0))

    if not any(not occurrence.is_unplaced for occurrence in rendered):
        # **Nothing is placed, so there is nothing to group against.** The file
        # reads exactly as it always has — one flat list, no headings, and no
        # `Etapiga sidumata` announcing a gap that cannot be closed.
        #
        # **Including when the file says where it is.** A Matter carrying an
        # `Õigusakt` and a `Hetkeseis` and no recorded step is the ordinary
        # register row, and for a while this grouped it: the current phase alone
        # made `grouped` true, so every row on every such file fell under an
        # `Etapiga sidumata` heading with a sentence explaining itself. A heading
        # needs something to contrast with, and «where the file is now» with no
        # rows in it is not that — it is what the header band has always said
        # (docs/adr/0092 §16, docs/adr/0098 §5).
        return items, PhaseHistory()

    empty_current = (
        current_without_rows
        if current_without_rows is not None and current_without_rows not in rendered
        else None
    )
    shown = tuple(rendered) if empty_current is None else (empty_current, *rendered)
    return ordered, PhaseHistory(
        occurrences=shown,
        current_without_rows=empty_current,
        grouped=True,
    )


__all__ = ["UNPLACED_KEY", "PhaseHistory", "PhaseOccurrence", "build", "business_day"]
