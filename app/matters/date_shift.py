"""«Nihuta kuupäeva» — move an internal task's day by a day, a week or a month.

A faster way into the ordinary `Muuda`, not a workflow of its own. It changes
the *plan*: the day a lawyer set for their own `Teen` step. It never touches a
statutory or external date, which is why the label is «Nihuta kuupäeva» and not
«Pikenda tähtaega» (docs/adr/0143, amendment of 2026-10-26).

**Which rows.** A `NextAction` that is

* current (`OPEN`) or planned (`PLANNED`), on an open Matter;
* `ActionKind.DO` with `DateSemantics.DEADLINE` — a lawyer's own task;
* recorded to an exact day (`DatePrecision.EXACT`) — moving «oktoober 2026» by
  a week would invent the day it is moved from;
* not an `Arvamuse järelkontroll` (its day moves through its own form,
  docs/adr/0146 §6);
* carrying a responsible person — the canonical edit assigns an unnamed step to
  the Matter's owner, and a date-only change must assign nobody.

Everything else — `Arvamuse tähtaeg`, `Oluline tähtaeg`, a commencement, a
feedback wait, a WAIT/MONITOR review date, an approximate or undated step, a
finished one — is deliberately outside it and keeps the full `Muuda` only.

**How.** One server-side operation for every entry point. Under the Matter's
lock it re-reads the named action, checks it is the one the person reviewed
(same row, still eligible, same day), computes the new day from the *stored*
one and hands it to the canonical edit with every other value read off the
locked row: `workspace.change_current_action` for the current step (it
supersedes the row and carries the plan step and the restriction) and
`workflow.services.change_planned_action` for a planned one. The audit trail is
theirs: a `NEXT_ACTION_SET` naming the row it replaced, which still holds the
old day.

A second press of the same form names a row that has just been superseded, so
it is refused as stale and nothing moves twice.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.db import transaction

from app.core.dates import add_months, format_estonian_date
from app.core.errors import DomainError
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from app.workflow.models import NextAction

#: The three moves, in the order they are offered. Calendar arithmetic: a month
#: is a calendar month, clamped to the shorter month (31.1 → 28.2 or 29.2).
SHIFTS: tuple[tuple[str, str, Callable[[date], date]], ...] = (
    ("paev", "+1 päev", lambda day: day + timedelta(days=1)),
    ("nadal", "+1 nädal", lambda day: day + timedelta(weeks=1)),
    ("kuu", "+1 kuu", lambda day: add_months(day, 1)),
)
SHIFT_KEYS = tuple(key for key, _label, _move in SHIFTS)

#: The page the form was drawn on no longer describes this step.
SHIFT_STALE = (
    "Tegevus või selle kuupäev on vahepeal muutunud. Kuupäeva ei muudetud — "
    "vaata praegust seisu ja proovi uuesti."
)
#: A row this control does not move (see the module docstring).
SHIFT_NOT_ELIGIBLE = "Selle kirje kuupäeva siit nihutada ei saa. Kasuta «Muuda täpsemalt»."
SHIFT_UNKNOWN = "Tundmatu nihe."


def shifted(day: date, key: str) -> date:
    for name, _label, move in SHIFTS:
        if name == key:
            return move(day)
    raise DomainError(SHIFT_UNKNOWN)


def is_shiftable(action: NextAction) -> bool:
    """Whether this row is one «Nihuta kuupäeva» moves — the record, not the person.

    Reads the action and its Matter as loaded; the person's right to write is
    the view's question (`business_write_required`), asked separately.
    """
    return (
        action.status in (ActionStatus.OPEN, ActionStatus.PLANNED)
        and action.kind == ActionKind.DO
        and action.date_semantics == DateSemantics.DEADLINE
        and action.date_precision == DatePrecision.EXACT
        and action.target_date is not None
        and action.follow_up_id is None
        and action.responsible_id is not None
        and bool(action.matter.is_open)
    )


@dataclass(frozen=True)
class ShiftChoice:
    key: str
    label: str
    day: date

    @property
    def display(self) -> str:
        return format_estonian_date(self.day)


def choices(day: date) -> list[ShiftChoice]:
    """The three proposals for a stored day, each computed here, never in the browser."""
    return [ShiftChoice(key, label, move(day)) for key, label, move in SHIFTS]


@dataclass(frozen=True)
class ShiftResult:
    old_date: date
    new_date: date
    action: NextAction

    @property
    def message(self) -> str:
        """«Kuupäev muudetud: 5.11 → 12.11.» — the subtle confirmation."""
        old, new = self.old_date, self.new_date
        return f"Kuupäev muudetud: {old.day}.{old.month} → {new.day}.{new.month}."


@transaction.atomic
def shift_action_date(
    *,
    matter: Any,
    action_id: Any,
    actor: Any,
    shift: str,
    source_date: date,
    proposed_date: date | None = None,
) -> ShiftResult:
    """Move one named action's day, through the canonical edit — or refuse with nothing written.

    ``source_date`` is the day the person was shown; ``proposed_date`` (when the
    form sends it) the day they were shown it would become. Both are checked
    against the locked row: a step moved, finished or replaced in another tab,
    or a second press of the same form, refuses with `SHIFT_STALE`.

    The caller has already resolved ``action_id`` through ``visible_to`` on this
    ``matter``; it is re-read here under the lock, by Matter and id together, so
    a row of another Matter can never be the one that moves.
    """
    from app.matters.locks import lock_open_matter_for_business_write
    from app.matters.workspace import change_current_action
    from app.workflow.services import change_planned_action

    if shift not in SHIFT_KEYS:
        raise DomainError(SHIFT_UNKNOWN)
    try:
        locked_matter = lock_open_matter_for_business_write(matter.pk)
    except DomainError as error:
        raise DomainError(SHIFT_STALE) from error
    action = (
        NextAction.objects.select_for_update(no_key=True)
        .select_related("matter")
        .filter(matter=locked_matter, pk=action_id)
        .first()
    )
    if action is None or action.status not in (ActionStatus.OPEN, ActionStatus.PLANNED):
        raise DomainError(SHIFT_STALE)
    if action.target_date != source_date:
        raise DomainError(SHIFT_STALE)
    if not is_shiftable(action):
        raise DomainError(SHIFT_NOT_ELIGIBLE)
    old = source_date
    new = shifted(old, shift)
    if proposed_date is not None and proposed_date != new:
        raise DomainError(SHIFT_STALE)

    if action.status == ActionStatus.OPEN:
        replacement = change_current_action(
            matter=locked_matter,
            actor=actor,
            action_id=action.pk,
            text=action.text,
            kind=action.kind,
            date_semantics=action.date_semantics,
            target_date=new,
            date_precision=DatePrecision.EXACT,
            source_text=action.source_text,
            responsible=action.responsible,
        )
    else:
        replacement = change_planned_action(
            matter=locked_matter,
            action_id=action.pk,
            text=action.text,
            target_date=new,
            actor=actor,
        )
    return ShiftResult(old_date=old, new_date=new, action=replacement)
