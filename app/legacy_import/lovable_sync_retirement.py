"""A cohort member leaves its cohort only with its Teema's permanent deletion.

Audit 2026-11 C-L-04. The nightly synchronisation (docs/adr/0192) requires each
cohort to hold exactly its enrolled number of Teemad, so a lost state, a
vanished topic or a truncated read stops the night (COHORT_SIZE). A Teema a
person deletes permanently (docs/adr/0165) takes its state with it, and that is
not drift: it is an authorised, audited deletion. This module records it as a
`LovableSyncRetirement`, and the size a run requires is the enrolled size less
these rows (`lovable_sync.Cohort.required`). Nothing else re-sizes a cohort.

**Membership is proven, never assumed** (owner brief 2026-11-06 §17, REL-01).
A retirement links four things, and each link is checked by the model and again
by the database (migration 0024):

* the **cohort** and the **Lovable id**: a `LovableSyncMembership` of that
  cohort, that Teema and that id, written when the state was seeded from the
  cohort's approved provenance, or by the deletion from that provenance while
  it still existed;
* the **deleted Teema**: a tombstone;
* its **own deletion event**: that Teema's MATTER_DELETED event, whose purge
  removed a synchronisation state, which is not the source replacement, and
  whose actor is the retirement's actor.

Neither a reference that looks right nor a MATTER_DELETED event alone proves
membership, so where no membership exists nothing is recorded and `retire`
refuses, naming what is missing.

Two doors, one recorder (`record`):

* `record_for_deletion` — called by `app.matters.deletion.delete_matter` in the
  deletion's own transaction, after its MATTER_DELETED event, for each removed
  state whose membership `members_of` proved before the purge. A person's
  deletion is never refused because of the synchronisation: a state without
  proven membership is simply not retired, and the night then judges the
  cohort's size as drift, as before. The source-authoritative replacement
  (docs/adr/0203) records none, because its own enrolment retires the cohorts
  it replaces whole.
* `retire` — `lovable_sync retire`, dry-run unless ``apply``: an operator's
  entry for a deletion whose retirement was not recorded. It is refused unless
  the Teema is a tombstone with a MATTER_DELETED event whose purge removed a
  synchronisation state, a membership names exactly this cohort for it, no
  retirement names it yet, and the cohort is short. A Teema deleted before
  memberships were recorded (before migration 0024) has none, because the purge
  removed its state and provenance, and is refused (NO_MEMBERSHIP_EVIDENCE).

Neither writes a business record, changes the control row or a state, or opens
APPLY: a retirement is bookkeeping for the size check and nothing more.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from app.legacy_import import lovable_sync
from app.legacy_import.lovable_sync import SyncRefused
from app.legacy_import.lovable_sync_models import (
    STATE_LABEL,
    LovableSyncControl,
    LovableSyncMembership,
    LovableSyncRetirement,
    LovableSyncState,
    MembershipBasis,
    RetirementKind,
    removed_a_state,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DELETION_REASON",
    "STATE_LABEL",
    "Member",
    "RetirementPlan",
    "members_of",
    "record",
    "record_for_deletion",
    "retire",
]

#: What an automatic retirement says; the reason a person gave is on the deletion.
DELETION_REASON = "Teema kustutati jäädavalt (docs/adr/0165)."
#: The unique constraint of migration 0023: a Teema leaves a cohort once.
RETIRED_ONCE = "lovable_sync_retired_once"


@dataclass(frozen=True)
class Member:
    """A state the purge is about to remove, read before it goes, and whether its
    membership of its cohort under its Lovable id is proven (REL-01)."""

    cohort: str
    source_id: Any
    reference: str
    evidenced: bool = False


def _provenance_names(cohort: lovable_sync.Cohort, matter_id: Any, source_id: Any) -> bool:
    """Whether the cohort's approved provenance names this Teema under this
    Lovable id: exactly one canonical source reference among the cohort's, with
    that id, as `enroll` required when it seeded the state. Read before the
    purge removes the references and the provenance records."""
    found = [r for r in lovable_sync.cohort_references(cohort) if r.matter_id == matter_id]
    if len(found) != 1:
        return False
    named = ((found[0].source_row_raw or {}).get("lovable") or {}).get("id")
    return bool(named) and str(named) == str(source_id)


def members_of(state_ids: Iterable[Any]) -> list[Member]:
    """The states a deletion will remove, as the recorder needs them afterwards.

    Called by `delete_matter` under the Teema's row lock, before the tombstone is
    marked and before the purge. For each state of a cohort the nightly run
    synchronises, membership is proven by a `LovableSyncMembership` of that
    cohort with that Lovable id. A state seeded before memberships were recorded
    (migration 0024) has none yet: its membership is recorded now (basis
    DELETION) only if the cohort's approved provenance names this Teema under
    this id, the same test `enroll` made. Otherwise the member is unproven and
    will not be retired; the deletion itself goes on."""
    active = {cohort.key: cohort for cohort in lovable_sync.cohorts()}
    members: list[Member] = []
    for cohort_key, source_id, reference, matter_id in LovableSyncState._base_manager.filter(
        pk__in=list(state_ids)
    ).values_list("cohort", "source_id", "reference", "matter_id"):
        evidenced = False
        cohort = active.get(cohort_key)
        if cohort is not None:
            known = LovableSyncMembership.objects.filter(
                cohort=cohort_key, matter_id=matter_id
            ).first()
            if known is not None:
                evidenced = str(known.source_id) == str(source_id)
            elif _provenance_names(cohort, matter_id, source_id):
                lovable_sync.remember_membership(
                    cohort=cohort_key,
                    matter_id=matter_id,
                    source_id=source_id,
                    basis=MembershipBasis.DELETION,
                )
                evidenced = True
            if not evidenced:
                # Counts only: the night will report the cohort's size as drift.
                logger.warning(
                    "lovable_sync_retirement: a deleted Teema's state in %s has no proven "
                    "membership; no retirement is recorded",
                    cohort_key,
                )
        members.append(Member(cohort_key, source_id, reference, evidenced))
    return members


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
    second retirement of the Teema under another deletion event is refused. Any
    other refusal (the model's, or the database's) is raised as it is."""
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
        diag = getattr(getattr(error, "__cause__", None), "diag", None)
        if getattr(diag, "constraint_name", None) != RETIRED_ONCE:
            raise
        raise SyncRefused("ALREADY_RETIRED", "This Teema has already left its cohort.") from error


def record_for_deletion(
    *, matter: Any, members: list[Member], deletion_event: Any, actor: Any
) -> list[LovableSyncRetirement]:
    """The deletion's own retirements: one per removed state whose membership of a
    cohort the nightly run synchronises was proven (`members_of`). Called only
    from `delete_matter`, inside its transaction."""
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
        if member.evidenced and member.cohort in active
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
    #: The membership that proves the Teema was in ``cohort``, and its Lovable id.
    membership_id: Any = None
    source_id: Any = None


def _held(cohort: str) -> int:
    return LovableSyncState.objects.filter(cohort=cohort).count()


#: What `retire` says when no membership links the Teema to any cohort.
NO_MEMBERSHIP_EVIDENCE = (
    "No cohort membership record names this Teema. Its deletion removed a "
    "synchronisation state, but the purge also removed that state and the Teema's "
    "import provenance, so nothing left in the database links the cohort, the "
    "Lovable id and this Teema: it was deleted before memberships were recorded "
    "(migration legacy_import.0024), or its state was never enrolled from a "
    "cohort's provenance. A similar reference or the deletion event alone does not "
    "prove membership; nothing was written."
)


def retire(*, cohort: str, reference: str, reason: str, apply: bool = False) -> RetirementPlan:
    """An operator's retirement of one permanently deleted cohort Teema.

    Refused (`SyncRefused`, nothing written) unless: a reason is given; final
    cutover is not recorded; ``cohort`` is synchronised now; ``reference`` names
    exactly one Teema and it is a tombstone (a live Teema leaves only through
    its deletion); its MATTER_DELETED event exists, is a person's deletion and
    not the replacement, and its purge removed a synchronisation state; a
    `LovableSyncMembership` names this Teema in exactly ``cohort`` (none at all:
    NO_MEMBERSHIP_EVIDENCE; another cohort's: WRONG_COHORT); no retirement names
    it yet; it holds no state now; and the cohort is short of its required size.
    The retirement takes its Lovable id from the membership. Under the run lock,
    so a night never reads half of it."""
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
        if not removed_a_state(payload):
            raise SyncRefused("NOT_A_MEMBER", "The deleted Teema held no synchronisation state.")
        memberships = {m.cohort: m for m in LovableSyncMembership.objects.filter(matter=matter)}
        if not memberships:
            raise SyncRefused("NO_MEMBERSHIP_EVIDENCE", NO_MEMBERSHIP_EVIDENCE)
        membership = memberships.get(cohort)
        if membership is None:
            raise SyncRefused(
                "WRONG_COHORT",
                f"The membership records name this Teema in {', '.join(sorted(memberships))}, "
                f"not in {cohort}; nothing was written.",
            )
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
            membership_id=membership.pk,
            source_id=membership.source_id,
        )
        if apply:
            record(
                cohort=cohort,
                matter=matter,
                deletion_event=event,
                actor=event.actor,
                source_id=membership.source_id,
                reference=plan.reference,
                kind=RetirementKind.OPERATOR,
                reason=reason,
            )
    return plan
