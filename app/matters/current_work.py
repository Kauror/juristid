"""`PRAEGUNE TEGEVUS` rows in date order, whatever kind each row is (#525).

The zone lists three kinds of dated work: the current `Järgmiseks`
(`NextAction`, `OPEN`), the open consultation rounds waiting for feedback
(`MatterEngagement` with an open wait) and the planned future actions
(`NextAction`, `PLANNED`). Until #525 they were three blocks in a fixed order —
current, waits, planned — each sorted within itself, so a round due on
02.11 read *under* a step due on 04.11. The owner's rule is plain: **an earlier
day comes before a later one, regardless of the kind of row.**

**Ordering is display-only.** Nothing here decides which action is current,
when a planned action is promoted, what is late or what is waiting: each source
is read exactly as before (`selectors.current_action_of`,
`selectors.planned_actions_of`, the view's `_feedback_waits_of`), already
scoped to what this reader may see, and this module only interleaves them.

**The day a row sorts by is the day it shows.** A step's `target_date` — for an
approximate step the stored anchor of its period, the same value
`NextActionQuerySet.planned` already orders planned actions by (ADR 0079) — and
a round's `feedback_deadline`.

**Deterministic on a tie, and the undated last.** Rows with no day follow every
dated row and keep their own «Kuupäev määramata» / «Tähtaeg määramata» label.
On an equal day — and among the undated — the current step comes first, then
the rounds, then the planned actions: the block order the zone had before, so
a tie reads as it always did. Within one kind the source's own order stands
(the sort is stable), which is itself deterministic down to the primary key.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

RowKind = Literal["current", "wait", "planned"]

#: Tie order on an equal day: the zone's former block order.
_KIND_RANK: dict[str, int] = {"current": 0, "wait": 1, "planned": 2}


@dataclass(frozen=True)
class WorkRow:
    """One line of `PRAEGUNE TEGEVUS`: what it is, and the record it draws."""

    kind: RowKind
    record: Any

    @property
    def is_current(self) -> bool:
        return self.kind == "current"

    @property
    def is_wait(self) -> bool:
        return self.kind == "wait"

    @property
    def is_planned(self) -> bool:
        return self.kind == "planned"

    @property
    def day(self) -> date | None:
        """The day this row is ordered by, or ``None`` when none is recorded."""
        if self.kind == "wait":
            return self.record.feedback_deadline
        return self.record.target_date

    @property
    def dom_id(self) -> str:
        """A stable element id for a row that carries forms.

        The out-of-band refresh after a round's answer re-sends the whole list
        and keeps these rows' existing elements in place (`hx-preserve`), so
        whatever is typed in their forms survives. A planned follow-up check
        keeps the `jarelkontroll-<pk>` id Minu asjad links to (docs/adr/0146 §7);
        a current check carries that id on its task line instead.
        """
        if self.kind == "wait":
            return f"ootus-{self.record.pk}"
        if self.kind == "planned" and self.record.follow_up_id:
            return f"jarelkontroll-{self.record.pk}"
        return f"tegevus-{self.record.pk}"


def _sort_key(row: WorkRow) -> tuple[bool, date, int]:
    day = row.day
    return (day is None, day or date.min, _KIND_RANK[row.kind])


def chronological_rows(
    *,
    current_action: Any,
    feedback_waits: Iterable[Any],
    planned_actions: Sequence[Any],
) -> list[WorkRow]:
    """The zone's dated work in one list, earliest day first (#525)."""
    rows: list[WorkRow] = []
    if current_action is not None:
        rows.append(WorkRow("current", current_action))
    rows.extend(WorkRow("wait", wait) for wait in feedback_waits)
    rows.extend(WorkRow("planned", planned) for planned in planned_actions)
    return sorted(rows, key=_sort_key)
