"""A cohort member leaves its cohort only with its Teema's permanent deletion.

Audit 2026-11 C-L-04. The nightly synchronisation (docs/adr/0192) requires each
cohort to hold exactly its enrolled number of Teemad, so a lost state, a
vanished topic or a truncated read stops the night (COHORT_SIZE). A Teema a
person deletes permanently (docs/adr/0165) takes its state with it, and that is
not drift: it is an authorised, audited deletion. This module records it as a
`LovableSyncRetirement`, and the size a run requires is the enrolled size less
these rows (`lovable_sync.Cohort.required`). Nothing else re-sizes a cohort.

Two doors, one recorder (`record`):

* `record_for_deletion` — called by `app.matters.deletion.delete_matter` in the
  deletion's own transaction, after its MATTER_DELETED event, for each state the
  purge removed. A person's deletion is never refused because of the
  synchronisation; the source-authoritative replacement (docs/adr/0203) records
  none, because its own enrolment retires the cohorts it replaces whole.
* `retire` — `lovable_sync retire`, dry-run unless ``apply``: an operator's
  entry for a deletion made before the recorder existed. It is refused unless
  the Teema is a tombstone with a MATTER_DELETED event whose purge removed a
  synchronisation state, no retirement names it yet, and the cohort is short.

Neither writes a business record, changes the control row or a state, or opens
APPLY: a retirement is bookkeeping for the size check and nothing more.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from app.legacy_import import lovable_sync
from app.legacy_import.lovable_sync import SyncRefused
from app.legacy_import.lovable_sync_models import (
    LovableSyncControl,
    LovableSyncRetirement,
    LovableSyncState,
    RetirementKind,
)

#: The purge's own count of the states it removed, in the MATTER_DELETED payload.
STATE_LABEL = "legacy_import.LovableSyncState"
#: What an automatic retirement says; the reason a person gave is on the deletion.
DELETION_REASON = "Teema kustutati jäädavalt (docs/adr/0165)."


@dataclass(frozen=True)
class Member:
    """A state the purge is about to remove, read before it goes."""

    cohort: str
    source_id: Any
    reference: str


def members_of(state_ids: Iterable[Any]) -> list[Member]:
    """The states a deletion will remove, as the recorder needs them afterwards."""
    return [
        Member(cohort, source_id, reference)
        for cohort, source_id, reference in LovableSyncState._base_manager.filter(
            pk__in=list(state_ids)
        ).values_list("cohort", "source_id", "reference")
    ]


def record(
    *,
    cohort: str,
    matter: Any,
    deletion_event: Any,
    actor: Any,
    source_id: Any,
    reference: str,
    kind: str,
    reason: str,
) -> LovableSyncRetirement:
    """One retirement. The same deletion recorded again returns the first row; a
    second retirement of the Teema under another deletion event is refused."""
    existing = LovableSyncRetirement.objects.filter(matter=matter).first()
    if existing is not None:
        if existing.deletion_event_id == getattr(deletion_event, "pk", deletion_event):
            return existing
        raise SyncRefused("ALREADY_RETIRED", "This Teema has already left its cohort.")
    try:
        with transaction.atomic():
            return LovableSyncRetirement.objects.create(
                cohort=cohort,
                matter=matter,
                deletion_event=deletion_event,
                actor=actor,
                source_id=source_id,
                reference=(reference or "")[:20],
                kind=kind,
                retired_at=getattr(deletion_event, "occurred_at", None) or timezone.now(),
                reason=(reason or DELETION_REASON)[:300],
            )
    except IntegrityError as error:
        raise SyncRefused("ALREADY_RETIRED", "This Teema has already left its cohort.") from error


def record_for_deletion(
    *, matter: Any, members: list[Member], deletion_event: Any, actor: Any
) -> list[LovableSyncRetirement]:
    """The deletion's own retirements: one per removed state of a cohort the
    nightly run synchronises (a state of a retired cohort is no longer counted).
    Called only from `delete_matter`, inside its transaction."""
    active = {cohort.key for cohort in lovable_sync.cohorts()}
    return [
        record(
            cohort=member.cohort,
            matter=matter,
            deletion_event=deletion_event,
            actor=actor,
            source_id=member.source_id,
            reference=member.reference,
            kind=RetirementKind.MATTER_DELETED,
            reason=DELETION_REASON,
        )
        for member in members
        if member.cohort in active
    ]


@dataclass(frozen=True)
class RetirementPlan:
    """What `retire` found, and whether it recorded it."""

    cohort: str
    reference: str
    matter_id: Any
    deletion_event_id: Any
    deleted_at: Any
    held: int
    required: int
    applied: bool


def _held(cohort: str) -> int:
    return LovableSyncState.objects.filter(cohort=cohort).count()


def retire(*, cohort: str, reference: str, reason: str, apply: bool = False) -> RetirementPlan:
    """An operator's retirement of one permanently deleted cohort Teema.

    Refused (`SyncRefused`, nothing written) unless: a reason is given; final
    cutover is not recorded; ``cohort`` is synchronised now; ``reference`` names
    exactly one Teema and it is a tombstone (a live Teema leaves only through
    its deletion); its MATTER_DELETED event exists, is a person's deletion and
    not the replacement, and its purge removed a synchronisation state; no
    retirement names it yet; it holds no state now; and the cohort is short of
    its required size. Under the run lock, so a night never reads half of it."""
    from app.audit.enums import ChangeEventType
    from app.audit.models import ChangeEvent
    from app.matters.deletion import SOURCE_REPLACEMENT
    from app.matters.models import Matter

    reason = lovable_sync.text_value(reason)
    if not reason:
        raise SyncRefused("NO_REASON", "A retirement needs a reason.")
    ctl = LovableSyncControl.objects.filter(pk=1).first()
    if ctl is not None and ctl.cut_over:
        raise SyncRefused(
            "CUT_OVER", "Final cutover is recorded; the synchronisation never runs again."
        )
    parsed = Matter.parse_reference(reference or "")
    if parsed is None:
        raise SyncRefused("NOT_FOUND", "No Teema has that reference.")
    with lovable_sync.run_lock(), transaction.atomic():
        active = {c.key: c for c in lovable_sync.cohorts()}
        if cohort not in active:
            raise SyncRefused("UNKNOWN_COHORT", f"Cohort {cohort!r} is not synchronised.")
        found = list(
            Matter.all_objects.select_for_update().filter(
                reference_year=parsed[0], reference_number=parsed[1]
            )
        )
        if len(found) != 1:
            raise SyncRefused("NOT_FOUND", "No single Teema has that reference.")
        matter = found[0]
        if matter.deleted_at is None:
            raise SyncRefused(
                "NOT_DELETED",
                "A live Teema leaves its cohort only through its permanent deletion.",
            )
        event = (
            ChangeEvent.objects.filter(matter=matter, event_type=ChangeEventType.MATTER_DELETED)
            .order_by("occurred_at")
            .first()
        )
        if event is None:
            raise SyncRefused("NO_DELETION_AUDIT", "The Teema has no MATTER_DELETED event.")
        payload = event.payload or {}
        if (payload.get("provenance") or {}).get("source") == SOURCE_REPLACEMENT:
            raise SyncRefused(
                "REPLACEMENT",
                "The replacement's enrolment retires whole cohorts (docs/adr/0203 §3).",
            )
        if not (payload.get("rows") or {}).get(STATE_LABEL):
            raise SyncRefused("NOT_A_MEMBER", "The deleted Teema held no synchronisation state.")
        if LovableSyncRetirement.objects.filter(matter=matter).exists():
            raise SyncRefused("ALREADY_RETIRED", "This Teema has already left its cohort.")
        if LovableSyncState.objects.filter(matter=matter).exists():
            raise SyncRefused("STATE_PRESENT", "The Teema still holds a synchronisation state.")
        held, required = _held(cohort), active[cohort].required
        if held >= required:
            raise SyncRefused(
                "COHORT_WHOLE",
                f"Cohort {cohort} already holds {held} of its {required} Teemad.",
            )
        plan = RetirementPlan(
            cohort=cohort,
            reference=matter.display_reference or str(reference),
            matter_id=matter.pk,
            deletion_event_id=event.pk,
            deleted_at=matter.deleted_at,
            held=held,
            required=required,
            applied=apply,
        )
        if apply:
            record(
                cohort=cohort,
                matter=matter,
                deletion_event=event,
                actor=event.actor,
                source_id=None,
                reference=plan.reference,
                kind=RetirementKind.OPERATOR,
                reason=reason,
            )
    return plan
