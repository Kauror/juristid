"""Named use cases for `Järgmiseks`.

One Matter has at most one open action. Every path that changes it goes through
here so that the previous action is ended rather than overwritten, the audit
trail records who decided what, and the "one open action" invariant is
maintained in the same transaction as the change.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.apps import apps
from django.db import transaction
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.services import record_change_event
from app.core.errors import DomainError
from app.workflow.enums import (
    REVIEW_KINDS,
    ActionKind,
    ActionStatus,
    DatePrecision,
    DateSemantics,
)
from app.workflow.models import NextAction


def current_next_action(matter: Any) -> NextAction | None:
    """The one open action, or None. The only supported way to read it."""
    return (
        NextAction.objects.filter(matter=matter, status=ActionStatus.OPEN)
        .select_related("responsible")
        .first()
    )


@transaction.atomic
def set_next_action(
    *,
    matter: Any,
    text: str,
    kind: str = ActionKind.DO,
    date_semantics: str = DateSemantics.DEADLINE,
    target_date: date | None = None,
    date_precision: str = DatePrecision.EXACT,
    source_text: str = "",
    responsible: Any = None,
    actor: Any = None,
    provenance: dict[str, Any] | None = None,
) -> NextAction:
    """Set the current action, superseding whatever it replaces.

    Responsibility defaults to the Matter owner: in practice the person who
    owns the file is the person who acts on it, and forcing that choice on every
    routine update would slow the composer down for no gain.

    ``provenance`` is for the callers that are not a person: an importer or an
    enrichment run has no ``actor``, and "who set this" would otherwise read as
    a blank. It is recorded under its own key on the existing
    ``NEXT_ACTION_SET`` event rather than as a second event, because only one
    thing happened — an action was set — and a history that raised two rows for
    it would double every imported instruction in the timeline. Manual callers
    pass nothing and are unaffected.
    """
    text = text.strip()
    if not text:
        raise DomainError("Järgmiseks vajab teksti.")
    if kind not in ActionKind.values:
        raise DomainError(f"Tundmatu tegevuse liik {kind!r}.")
    if date_semantics not in DateSemantics.values:
        raise DomainError(f"Tundmatu kuupäeva tähendus {date_semantics!r}.")

    # A deadline with no date cannot be met, missed, planned against or
    # reported on. The form says so first for the user's sake, but the rule
    # belongs here, where an importer or an integration also has to obey it.
    if kind == ActionKind.DO and date_semantics == DateSemantics.DEADLINE and target_date is None:
        raise DomainError("Tähtajaline tegevus vajab kuupäeva.")

    # Lock the Matter, not just the action row. Closure and next-action changes
    # both depend on the Matter's lifecycle state, so the Matter row is the
    # concurrency boundary that keeps them from interleaving into a closed
    # Matter that still carries an open instruction.
    matter_model = apps.get_model("matters", "Matter")
    locked_matter = matter_model.objects.select_for_update().get(pk=matter.pk)
    if not locked_matter.is_open:
        raise DomainError("Suletud teemale ei saa järgmist tegevust määrata.")

    previous = (
        NextAction.objects.select_for_update()
        .filter(matter=locked_matter, status=ActionStatus.OPEN)
        .first()
    )
    if previous is not None:
        previous.status = ActionStatus.SUPERSEDED
        previous.ended_at = timezone.now()
        previous.ended_by = actor
        previous.save(update_fields=["status", "ended_at", "ended_by", "updated_at"])

    action = NextAction.objects.create(
        matter=locked_matter,
        text=text,
        kind=kind,
        date_semantics=date_semantics,
        target_date=target_date,
        date_precision=date_precision,
        source_text=source_text,
        responsible=responsible or locked_matter.owner,
        created_by=actor,
    )

    if previous is not None:
        # Written after the new row exists so the chain is navigable in both
        # directions without a nullable placeholder.
        previous.replaced_by = action
        previous.save(update_fields=["replaced_by", "updated_at"])

    payload: dict[str, Any] = {
        "kind": kind,
        "date_semantics": date_semantics,
        "target_date": target_date.isoformat() if target_date else None,
        "replaced": str(previous.id) if previous else None,
    }
    if provenance:
        # Nested rather than merged flat, so a provenance key can never shadow
        # one of the four above and silently change what the event says.
        payload["provenance"] = provenance

    record_change_event(
        event_type=ChangeEventType.NEXT_ACTION_SET,
        matter=locked_matter,
        actor=actor,
        obj=action,
        summary=text[:200],
        payload=payload,
    )
    return action


@transaction.atomic
def complete_next_action(*, action: NextAction, actor: Any = None) -> NextAction:
    """Mark the current action done. It stays in the history."""
    if action.status != ActionStatus.OPEN:
        raise DomainError("Ainult kehtivat tegevust saab lõpetada.")

    action.status = ActionStatus.COMPLETED
    action.ended_at = timezone.now()
    action.ended_by = actor
    action.save(update_fields=["status", "ended_at", "ended_by", "updated_at"])

    record_change_event(
        event_type=ChangeEventType.NEXT_ACTION_COMPLETED,
        matter=action.matter,
        actor=actor,
        obj=action,
        summary=action.text[:200],
        payload={"kind": action.kind},
    )
    return action


@transaction.atomic
def cancel_next_action(*, action: NextAction, actor: Any = None, reason: str = "") -> NextAction:
    if action.status != ActionStatus.OPEN:
        raise DomainError("Ainult kehtivat tegevust saab tühistada.")

    action.status = ActionStatus.CANCELLED
    action.ended_at = timezone.now()
    action.ended_by = actor
    action.save(update_fields=["status", "ended_at", "ended_by", "updated_at"])

    record_change_event(
        event_type=ChangeEventType.NEXT_ACTION_CANCELLED,
        matter=action.matter,
        actor=actor,
        obj=action,
        summary=action.text[:200],
        payload={"reason": reason[:500]},
    )
    return action


@transaction.atomic
def end_open_action_for_closure(*, matter: Any, actor: Any = None) -> NextAction | None:
    """Close out the open action when the Matter itself closes.

    A closed Matter with a live `Järgmiseks` would keep appearing in someone's
    work list forever.
    """
    action = current_next_action(matter)
    if action is None:
        return None
    return cancel_next_action(action=action, actor=actor, reason="Teema suleti")


@transaction.atomic
def acknowledge_review(
    *,
    action: NextAction,
    actor: Any = None,
    next_review_date: date | None = None,
    date_precision: str = DatePrecision.EXACT,
    note: str = "",
) -> NextAction:
    """Record that a WAIT or MONITOR was looked at, and when to look again.

    Without this, a review date that has passed leaves the row permanently
    "ripe": the only way to clear it is to edit the date, which looks like
    changing the plan rather than doing the work of checking. Reviewing is not
    completing — the Matter is still waiting on the same thing — so the action
    stays open and keeps its identity.
    """
    if action.status != ActionStatus.OPEN:
        raise DomainError("Ainult kehtivat tegevust saab üle vaadata.")
    if action.kind not in REVIEW_KINDS:
        raise DomainError("Üle vaadata saab ainult ootamist või jälgimist.")

    previous = action.target_date
    action.target_date = next_review_date
    action.date_precision = date_precision
    action.save(update_fields=["target_date", "date_precision", "updated_at"])

    record_change_event(
        event_type=ChangeEventType.NEXT_ACTION_REVIEWED,
        matter=action.matter,
        actor=actor,
        obj=action,
        summary=note[:200] or action.text[:200],
        payload={
            "from": previous.isoformat() if previous else None,
            "to": next_review_date.isoformat() if next_review_date else None,
            "kind": action.kind,
        },
    )
    return action
