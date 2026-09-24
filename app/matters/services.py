"""Named use cases for Matters.

Every business state change lives here. Views, forms and templates call these
functions; they never write model fields themselves. That is what makes the
audit trail complete, the invariants testable, and the same operation reusable
later from an importer or a scheduled job (master specification 12.4).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.operations import composer_operation
from app.audit.services import record_change_event
from app.core.enums import Visibility, most_restrictive, validate_visibility_override
from app.core.errors import DomainError
from app.core.richtext import excerpt, is_empty, sanitize_entry_html
from app.documents.enums import DocumentRole
from app.documents.services import add_evidence_version, create_document
from app.documents.uploads import read_upload
from app.matters.entry_enums import EntryKind
from app.matters.enums import (
    SELECTABLE_EXTERNAL_POSITION_PROVENANCE,
    DataQualityTier,
    EngagementKind,
    ExternalPositionProvenance,
    MatterDataClass,
    MatterOrigin,
    ProceduralLinkKind,
    RecordMode,
    TagAssignmentSource,
    WebsiteOverviewStatus,
)
from app.matters.locks import (
    lock_matter_for_evidence_integrity,
    lock_matter_for_write,
    lock_matters_in_order,
    lock_open_matter_for_business_write,
)
from app.matters.models import (
    DEVELOPMENT_TITLE_MAX_LENGTH,
    ENGAGEMENT_URL_MAX_LENGTH,
    EXTERNAL_POSITION_LAWYER_NOTE_MAX_LENGTH,
    EXTERNAL_POSITION_SOURCE_LABEL_MAX_LENGTH,
    EXTERNAL_POSITION_SUMMARY_MAX_LENGTH,
    EXTERNAL_POSITION_URL_MAX_LENGTH,
    PROCEDURAL_LINK_LABEL_MAX_LENGTH,
    PROCEDURAL_LINK_URL_MAX_LENGTH,
    WEBSITE_OVERVIEW_URL_MAX_LENGTH,
    Entry,
    EntryRevision,
    Matter,
    MatterAssignmentNotice,
    MatterEngagement,
    MatterExternalPosition,
    MatterPersonalNote,
    MatterProceduralDevelopment,
    MatterProceduralLink,
    MatterReferenceSequence,
    MatterWebsiteOverview,
    TagAssignment,
)
from app.submissions.models import Submission
from app.workflow.dates import period_starts_after
from app.workflow.enums import ActionStatus, DatePrecision, Disposition, Track
from app.workflow.models import NextAction
from app.workflow.services import (
    end_open_action_for_closure,
    set_next_action_for_new_work,
)

#: Distinguishes "leave this field alone" from "set this field to None".
_UNSET: Any = object()


@transaction.atomic
def allocate_matter_reference(year: int | None = None) -> tuple[int, int]:
    """Reserve the next ``YYYY_N`` reference for the given year.

    The row lock makes concurrent creation safe; the unique constraint on
    Matter is the backstop if anything ever bypasses this function.
    """
    reference_year = year or timezone.localdate().year
    MatterReferenceSequence.objects.get_or_create(year=reference_year)
    sequence = MatterReferenceSequence.objects.select_for_update().get(pk=reference_year)
    sequence.last_number += 1
    sequence.save(update_fields=["last_number", "updated_at"])
    return reference_year, sequence.last_number


@transaction.atomic
def reserve_matter_reference(year: int, number: int) -> int:
    """Make sure the sequence for ``year`` will never hand out ``number`` again.

    Called for every valid reference the register holds — including the ones it
    has merely *reserved*. The current year's sheet is pre-numbered ahead of
    use: the supplied snapshot runs to ``2026_300`` while only 192 rows carry a
    matter. Those unused numbers are still spoken for on paper, so a sequence
    that only knew about imported rows would hand ``2026_193`` to the next
    natively created Matter and collide with a file a lawyer already has open.

    Idempotent, and takes the same row lock as allocation, so an import running
    beside ordinary work cannot interleave badly.
    """
    MatterReferenceSequence.objects.get_or_create(year=year)
    sequence = MatterReferenceSequence.objects.select_for_update().get(pk=year)
    if number > sequence.last_number:
        sequence.last_number = number
        sequence.save(update_fields=["last_number", "updated_at"])
    return sequence.last_number


@transaction.atomic
def create_imported_matter(
    *,
    title: str,
    reference_year: int | None,
    reference_number: int | None,
    actor: Any = None,
    record_mode: str = RecordMode.ARCHIVE,
    source_organisations: Any = None,
    **extra: Any,
) -> Matter:
    """Create a Matter from a legacy register row, keeping its own reference.

    Distinct from :func:`create_matter` in exactly one respect that matters: it
    never allocates a new number. An imported row arrives with the reference the
    department has used for years, and issuing it a second, different one would
    break the only identifier anyone carries in their head.

    The sequence is still pushed forward, so native creation after an import
    cannot collide with it.
    """
    if not title.strip():
        raise DomainError("Teema vajab pealkirja.")

    matter = create_matter(
        title=title,
        actor=actor,
        assign_reference=False,
        record_mode=record_mode,
        origin=MatterOrigin.LEGACY_IMPORT,
        reporting_year=reference_year,
        # Named rather than left in `**extra` so the plural sender contract is
        # visible at the importer's entry point too. An era whose contract
        # resolves one sender passes a one-element list; an era whose
        # counterparty column meant the addressee passes nothing at all, and the
        # direction is still decided by the contract and never here
        # (Agent-E brief 19, 48).
        source_organisations=source_organisations,
        **extra,
    )

    if reference_year is not None and reference_number is not None:
        matter.reference_year = reference_year
        matter.reference_number = reference_number
        matter.save(update_fields=["reference_year", "reference_number", "updated_at"])
        reserve_matter_reference(reference_year, reference_number)

    return matter


@transaction.atomic
def create_matter(
    *,
    title: str,
    actor: Any = None,
    owner: Any = None,
    assign_reference: bool = True,
    reference_year: int | None = None,
    record_mode: str = RecordMode.FULL,
    origin: str = MatterOrigin.NATIVE,
    visibility: str = Visibility.NORMAL,
    data_class: str = MatterDataClass.REAL,
    source_organisations: Any = None,
    provenance: dict[str, Any] | None = None,
    **extra: Any,
) -> Matter:
    """Create a Matter. Only the title is required (specification 3.8).

    ``data_class`` defaults to REAL, which is what makes every importer, every
    fixture and every existing caller keep producing business data without
    being changed (Agent-C brief 29).

    ``source_organisations`` is named rather than left to ``**extra`` because it
    is a relation and not a column: it cannot be passed to ``objects.create``
    and has to be written once the Matter has a primary key. Keeping it in the
    signature is what stops a caller handing a list to a keyword argument that
    used to take one organisation and getting a confusing ``ValueError`` from
    deep inside the ORM (Agent-E brief 18).

    ``provenance`` means the same thing here as it does in
    :func:`assign_matter`: this record, and the ownership on it, was
    materialised by an operation rather than chosen by a colleague. It is what a
    seeding command passes so that a synthetic world does not open with a queue
    of «Uus asi» rows nobody was told anything by. Creating a Matter *is* an
    assignment when it names an owner, and this is the second half of the same
    boundary — the owner column is written here, directly, and never reaches
    :func:`assign_matter` (docs/adr/0051).
    """
    if not title.strip():
        raise DomainError("Teema vajab pealkirja.")
    if visibility not in Visibility.values:
        raise DomainError(f"Tundmatu nähtavus {visibility!r}.")
    validate_test_classification(data_class=data_class, origin=origin)
    track = extra.get("track", "")
    if track and track not in Track.values:
        raise DomainError(f"Tundmatu menetlusliik {track!r}.")

    year_number: tuple[int, int] | None = None
    if assign_reference:
        year_number = allocate_matter_reference(reference_year)

    # Free text, not taxonomy. Trimmed and length-capped here so it cannot
    # arrive as whitespace or overflow the column, and deliberately *not*
    # turned into a PolicyArea or a Tag (Stage-2E.1 brief 20).
    other_area = str(extra.pop("policy_area_other", "") or "").strip()
    if other_area:
        extra["policy_area_other"] = other_area[:400]

    # Same treatment as `policy_area_other` directly above, and for the same
    # reason: one place normalises it, so two callers cannot store the same
    # answer two ways. It is cleared rather than kept when `Muu` is not among
    # the chosen instruments — that decision belongs to the form, which knows
    # what was ticked; what this guarantees is only the shape.
    other_instrument = str(extra.pop("legal_instrument_other", "") or "").strip()
    if other_instrument:
        extra["legal_instrument_other"] = other_instrument[:400]

    policy_areas = extra.pop("policy_areas", None)
    # A relation, not a column, so it cannot go to `objects.create` and is
    # written once the Matter has a primary key — exactly like `policy_areas`.
    # Named in `extra` rather than in the signature because every caller that
    # sets it is a form, and adding a keyword to a service twenty callers share
    # to serve one of them buys nothing (task §17).
    legal_instruments = extra.pop("legal_instruments", None)
    # Validated before the Matter exists, so a bad sender fails the whole
    # creation rather than leaving a titled Matter behind with no senders.
    senders = normalize_source_organisations(source_organisations)

    matter = Matter.objects.create(
        title=title.strip(),
        owner=owner,
        record_mode=record_mode,
        origin=origin,
        visibility=visibility,
        data_class=data_class,
        reference_year=year_number[0] if year_number else None,
        reference_number=year_number[1] if year_number else None,
        reporting_year=extra.pop("reporting_year", year_number[0] if year_number else None),
        **extra,
    )
    if policy_areas:
        matter.policy_areas.set(policy_areas)
    if legal_instruments:
        # `.set()` and not `.add()`: a list that names the same type twice is
        # one relation, because that is what the relation means.
        matter.legal_instruments.set(legal_instruments)
    if senders:
        matter.source_organisations.set(senders)

    record_change_event(
        event_type=ChangeEventType.MATTER_CREATED,
        matter=matter,
        actor=actor,
        obj=matter,
        summary=matter.title[:200],
        payload={
            "reference": matter.display_reference,
            "record_mode": matter.record_mode,
            "origin": matter.origin,
            # Carried on the creation event rather than raising a second one.
            # MATTER_CREATED already says what kind of record this is; a
            # separate MATTER_DATA_CLASS_CHANGED beside it would describe a
            # change that never happened (Agent-C brief 17).
            "data_class": matter.data_class,
            **(provenance or {}),
        },
    )

    # A native Matter somebody created with an owner on it is a hand-over like
    # any other, and it never passes through `assign_matter` — the column is
    # written above. An imported or OneNote-derived record with an owner is not:
    # nobody was told anything, the ownership is a fact reconstructed from a
    # spreadsheet cell, and the day of the import is not the day it landed on
    # that person's desk (docs/adr/0051, §26 no historical backfill).
    if provenance is None and origin == MatterOrigin.NATIVE:
        _raise_assignment_notice(matter=matter, owner=owner, actor=actor)
    return matter


def validate_test_classification(*, data_class: str, origin: str) -> None:
    """Refuse an unknown class, and refuse TEST on anything not created here.

    The second rule is the load-bearing one. TEST means "made while developing
    Juristid", so the only Matter that can honestly carry it is one this system
    created. An imported register row is somebody's real work from years ago,
    with provenance that cannot be reconstructed, and marking it disposable
    because a control sat next to the wrong row is the single most expensive
    mistake this feature could enable.

    Mirrored by the ``matters_test_data_is_native`` database constraint, because
    this function is not the only thing that can write the column
    (Agent-C brief 12, 17, 48).
    """
    if data_class not in MatterDataClass.values:
        raise DomainError(f"Tundmatu andmeklass {data_class!r}.")
    if data_class == MatterDataClass.TEST and origin != MatterOrigin.NATIVE:
        raise DomainError(
            "Testandmeteks saab märkida ainult süsteemis loodud teema. "
            "Ajalooline või imporditud kirje jääb alati pärisandmeteks."
        )


@transaction.atomic
def set_matter_data_class(*, matter: Matter, data_class: str, actor: Any = None) -> Matter:
    """Reclassify a Matter as real business data or as development data.

    Both directions are supported and both are ordinary. A development record
    created without ticking the box is the common case; the opposite —
    a real matter somebody opened while demonstrating the system — happens too.

    Child records are deliberately left alone. Their testness is derived from
    this Matter every time it is read, exactly as their visibility is, so
    nothing here can go stale and no combination of a REAL Matter with a TEST
    submission is representable (Agent-C brief 18, 20).
    """
    validate_test_classification(data_class=data_class, origin=matter.origin)

    previous = matter.data_class
    if previous == data_class:
        return matter

    matter.data_class = data_class
    matter.save(update_fields=["data_class", "updated_at"])

    record_change_event(
        event_type=ChangeEventType.MATTER_DATA_CLASS_CHANGED,
        matter=matter,
        actor=actor,
        obj=matter,
        summary=f"{previous} → {data_class}",
        payload={"from": previous, "to": data_class},
    )
    return matter


def _submissions_left_above_their_evidence(*, matter: Matter, visibility: str) -> int:
    """How many submissions this Matter visibility would leave above their evidence.

    A Submission's final evidence may never be less restricted than the
    Submission itself, or the exact text of a restricted opinion is listed and
    downloadable by people who cannot see the opinion at all (docs/adr/0011).
    Both sides of that comparison are derived from the Matter's visibility, so
    the Matter is the third thing that can break the rule — and unlike the other
    two it does so without either record being written.

    It breaks in one direction only. Tightening the Matter raises both sides
    together; relaxing it drops the evidence to whatever its own override says
    while a Submission carrying its own RESTRICTED override stays where it is.
    The comparison is written out in full rather than reduced to that one case,
    so it stays correct if the vocabulary ever gains a third value.

    A Matter has a handful of submissions, and this reads two columns of them.
    """
    rows = (
        Submission.objects.filter(matter=matter, final_version__isnull=False)
        .order_by()
        .values_list("visibility_override", "final_version__document__visibility_override")
    )
    stranded = 0
    for submission_override, evidence_override in rows:
        submission_effective = most_restrictive(
            visibility, submission_override or Visibility.NORMAL
        )
        evidence_effective = most_restrictive(visibility, evidence_override or Visibility.NORMAL)
        if most_restrictive(evidence_effective, submission_effective) != evidence_effective:
            stranded += 1
    return stranded


@transaction.atomic
def set_matter_visibility(*, matter: Matter, visibility: str, actor: Any = None) -> Matter:
    """Change a Matter's visibility, audited.

    Child records need no update: their effective visibility is derived from
    this value every time it is read, so tightening the Matter tightens every
    child immediately and relaxing it leaves individually restricted children
    restricted. Nothing here can go stale, and a write that bypasses this
    function changes what children are visible just as correctly — it only
    misses the audit record (docs/adr/0005).

    The one derived relationship that does not survive the change on its own is
    a Submission's final evidence, which is refused here rather than left to
    become false. See `_submissions_left_above_their_evidence`.

    That refusal is only as good as the moment it is evaluated in. Binding final
    evidence writes the Submission, not the Matter, so before DATA-002 the two
    operations touched no row in common: each could read a database in which the
    other had not committed, each pass, and both commit. The count below is
    therefore taken under the Matter's own row lock, after which it sees every
    pointer committed up to that instant — and any binding transaction still in
    flight is queued behind this one rather than invisible to it
    (app/matters/locks.py, docs/adr/0040).
    """
    if visibility not in Visibility.values:
        raise DomainError(f"Tundmatu nähtavus {visibility!r}.")

    # A cheap read of the caller's instance, only to skip the lock for the
    # common no-op. Everything the decision rests on is re-read below.
    if matter.visibility == visibility:
        return matter

    locked = lock_matter_for_evidence_integrity(matter.pk)

    # Re-read, not remembered: this transaction may have waited here, and what
    # it knew before waiting is exactly what the wait invalidates.
    previous = locked.visibility
    if previous == visibility:
        return matter

    stranded = _submissions_left_above_their_evidence(matter=locked, visibility=visibility)
    if stranded:
        # Counted, never named: the submissions this refers to are the
        # restricted ones, and the person editing the Matter is not necessarily
        # someone who may read them.
        raise DomainError(
            f"Selle nähtavusega jääks {stranded} arvamuse lõplik tõend arvamusest endast "
            "vähem piiratuks. Piira enne nende arvamuste tõenddokumente või muuda "
            "arvamuste enda piirangut."
        )

    locked.visibility = visibility
    locked.save(update_fields=["visibility", "updated_at"])

    record_change_event(
        event_type=ChangeEventType.MATTER_VISIBILITY_CHANGED,
        matter=locked,
        actor=actor,
        obj=locked,
        payload={"from": previous, "to": visibility},
    )
    matter.visibility = visibility
    return matter


# ---------------------------------------------------------------------------
# Ownership, stage and the operational fields
# ---------------------------------------------------------------------------


def _is_human_actor(actor: Any) -> bool:
    """Whether a real, persisted person is performing this operation.

    Not a caller-name heuristic and deliberately not one: ``AnonymousUser``
    answers ``False`` to ``is_authenticated``, an unsaved instance has no ``pk``,
    and a management command that passes no actor at all has nothing here.
    Every other way of asking — inspecting the stack, checking the module the
    call came from — would be a guess that a refactor silently changes.
    """
    return (
        actor is not None
        and getattr(actor, "pk", None) is not None
        and bool(getattr(actor, "is_authenticated", False))
    )


def _deactivate_assignment_notices(*, matter: Matter, at: Any) -> int:
    """Retire every still-active notice about this Matter.

    Called before ownership moves, and it is what stops a stale one being
    offered. Sandra was handed a file at 09:00 and has not looked at it; at
    09:05 it goes to Ireen. Sandra's ``Uus asi`` must not still open a Matter
    that is no longer hers — and the same is true when the owner is cleared and
    the file is on nobody's desk (docs/adr/0051).

    The row is stamped, never deleted: what landed on somebody's desk is a fact
    about that day, and the receipt is the only place it is recorded.
    """
    return MatterAssignmentNotice.objects.filter(
        matter=matter, viewed_at__isnull=True, superseded_at__isnull=True
    ).update(superseded_at=at, updated_at=at)


def _raise_assignment_notice(*, matter: Matter, owner: Any, actor: Any) -> None:
    """Tell the new owner, when a colleague is what put the file there.

    Three conditions, and each excludes a different thing that is not somebody
    handing over work:

    * an owner — an unassigned Matter has nobody to tell;
    * a human actor — the owner backfill, an import and a seeding command all
      materialise ownership without a colleague deciding anything;
    * ``provenance`` absent at the call site, which is how a system operation
      that *does* run under an operator's account says so (``assign_matter``).

    **A fourth condition since QA-022: ``actor is owner`` is excluded.**

    It used not to be, on the argument that «a person returning to Minu asjad
    sees what has arrived, and something they filed themselves an hour ago is
    exactly that». A working session showed what that argument misses: filing
    your own Teema is the ordinary way work starts here, so `UUS ASI` filled up
    with five files the reader had created themselves that morning — and a
    block whose whole job is «look at this, it is new to you» is worth nothing
    once most of it is not.

    `Uus asi` means somebody handed you work. Handing it to yourself is not an
    arrival; you were there when it happened.

    **What still raises one is unchanged**: a colleague assigning the file to
    you, a transfer of ownership to you by somebody else, any assignment whose
    actor is another person. The test is on this single act, not on whether the
    recipient has touched the Matter before — a lawyer who filed a Teema in
    March and is handed it back in September is being handed work, and hears
    about it.
    """
    if owner is None or not _is_human_actor(actor):
        return
    if actor is not None and getattr(actor, "pk", None) == getattr(owner, "pk", None):
        return
    MatterAssignmentNotice.objects.create(matter=matter, recipient=owner, assigned_by=actor)


@transaction.atomic
def acknowledge_assignment_notice(*, notice: MatterAssignmentNotice, actor: Any) -> bool:
    """Mark one notice read, because its recipient opened it from the block.

    Deliberately not reachable from an ordinary Matter GET. "Viewed" here means
    *this person acted on this notice*, and it has to, because self-assignment
    is a supported case: creating a Matter and naming yourself the owner
    redirects you straight into it, and if merely rendering that page counted as
    acknowledgement the block would clear itself before it was ever seen
    (docs/adr/0051).

    One conditional UPDATE, so a double submit is idempotent rather than a
    second stamp, and returns whether this call was the one that set it.

    ``superseded_at`` is in that condition as well as in the route's own lookup,
    and not as a duplicate of it. The route refuses a stale form; this refuses
    to stamp a retired row at all, whoever asks. A notice that stopped being
    active because the file changed hands has already reached its terminal
    state, and recording that somebody *viewed* it afterwards would conflate the
    two reasons a receipt closes — which is the one thing the two columns exist
    to keep apart (docs/adr/0051).
    """
    if notice.recipient_id != getattr(actor, "pk", None):
        raise DomainError("Teavitus kuulub teisele inimesele.")
    now = timezone.now()
    updated = MatterAssignmentNotice.objects.filter(
        pk=notice.pk,
        recipient=actor,
        viewed_at__isnull=True,
        superseded_at__isnull=True,
    ).update(viewed_at=now, updated_at=now)
    return bool(updated)


#: What an owner change that arrived after the Matter was deleted is told.
ASSIGNMENT_ON_DELETED_MATTER = "Teemat ei ole enam olemas, seega ei saa sellele vastutajat määrata."


@transaction.atomic
def assign_matter(
    *, matter: Matter, owner: Any, actor: Any = None, provenance: dict[str, Any] | None = None
) -> Matter:
    """Give the Matter an owner, or hand it to someone else.

    ``provenance`` is for the assignments no colleague made: the owner backfill
    derives ownership from imported register cells, and the event has to say so
    — which era, which row, and by which resolution rule — or a reader months
    later cannot tell an attested mapping from an inference. It is merged into
    the change-event payload rather than kept in a second table, because this
    *is* the assignment event and one record is easier to trust than two
    (Stage-2F brief 9).

    It is also the one boundary «Uus asi» reads. An assignment carrying
    ``provenance`` was made by an operation and not by a colleague, so it
    retires the previous owner's notice without raising a new one; an assignment
    a person performed does both. The early return below is what makes a no-op
    POST — the same owner submitted again — produce no second notice, because
    nothing was assigned (docs/adr/0051).

    **The Matter is locked first, and everything is read from the locked row**
    (ENG-075). This was the one Matter write that did not start with the Matter
    lock: it retired notices and then saved, while `delete_matter` locks the
    Matter and then deletes the notices — the opposite order, so the two could
    deadlock, or a stale assignment committed after the deletion and left an
    owner, a live notice and a MATTER_ASSIGNED event on a tombstone. Now the
    Matter row is taken at `FOR NO KEY UPDATE` before anything else, a deleted
    one is refused, and the audit's `from` is the owner the row holds under the
    lock rather than the one the request started with.

    **A closed Matter may still change hands.** That has always been allowed —
    correcting who owned a finished file is ordinary — so this refuses only a
    deleted Matter, never a closed one.
    """
    locked = Matter.all_objects.select_for_update(no_key=True).get(pk=matter.pk)
    if locked.deleted_at is not None:
        raise DomainError(ASSIGNMENT_ON_DELETED_MATTER)
    previous = locked.owner
    # The caller's instance learns the current owner either way, so a no-op
    # answer does not leave it believing in an owner the row no longer has.
    matter.owner = previous
    if previous == owner:
        return matter

    # Before the column moves, and inside this transaction: whatever the
    # previous owner was still holding unread is about a state that is ending.
    _deactivate_assignment_notices(matter=matter, at=timezone.now())

    matter.owner = owner
    matter.save(update_fields=["owner", "updated_at"])

    # The open next step follows the file, when it was following the file.
    #
    # `set_next_action` defaults `responsible` to the Matter's owner, so an
    # action nobody named a person for is the *owner's* action. Handing the
    # Matter over used to leave that action pointing at the previous owner: the
    # new owner opened `Minu töö` and their own file's next step was not there,
    # while somebody who no longer owns it still had it in their queue. That is
    # the "TEEN with a future date does not show up" report.
    #
    # Only when the responsible person *is* the previous owner. Somebody
    # deliberately made responsible for one step on a colleague's file stays
    # responsible — reassigning that would be the system overruling a decision
    # a person made (Teema QA §4).
    moved = None
    if previous is not None:
        moved = NextAction.objects.filter(
            matter=matter, status=ActionStatus.OPEN, responsible=previous
        ).first()
        if moved is not None:
            moved.responsible = owner
            moved.save(update_fields=["responsible", "updated_at"])

    record_change_event(
        event_type=ChangeEventType.MATTER_ASSIGNED,
        matter=matter,
        actor=actor,
        obj=matter,
        summary=getattr(owner, "display_name", "") or "",
        payload={
            "from_name": getattr(previous, "display_name", None),
            "to_name": getattr(owner, "display_name", None),
            # Named in the payload rather than raised as a second event: one
            # thing happened — the file changed hands — and the step going with
            # it is part of that, not a separate decision somebody made.
            "next_action_moved": str(moved.pk) if moved is not None else None,
            **(provenance or {}),
        },
    )

    # Only a colleague's decision reaches somebody's desk. `provenance` is
    # present exactly when no colleague made this one — the owner backfill
    # derives ownership from imported register cells — and a queue of «uus asi»
    # rows for work materialised by a batch is noise rather than news.
    if provenance is None:
        _raise_assignment_notice(matter=matter, owner=owner, actor=actor)
    return matter


@transaction.atomic
def change_stage(*, matter: Matter, stage: Any, actor: Any = None) -> Matter:
    """Record where the external process now stands.

    A stage change says nothing about whether Koda is finished. `jõustunud`
    means the act entered into force, not that the file is closed; closure is a
    separate, deliberate decision (master specification 3.4).
    """
    previous = matter.stage
    if previous == stage:
        return matter

    matter.stage = stage
    matter.save(update_fields=["stage", "updated_at"])

    record_change_event(
        event_type=ChangeEventType.MATTER_STAGE_CHANGED,
        matter=matter,
        actor=actor,
        obj=matter,
        summary=getattr(stage, "label_et", "") or "",
        payload={
            "from_label": getattr(previous, "label_et", None),
            "to_label": getattr(stage, "label_et", None),
            # **The stable keys, beside the labels.** A label is what a reader
            # saw and is the department's to reword — version 2.0 of the
            # vocabulary reworded three of them without moving a row — so a
            # later surface that had to answer «which stage was this» from the
            # history could only match on a string that is allowed to change.
            # `Menetluse kulg` is that surface, and it is why these two keys
            # exist: an event written from here on says exactly which stage,
            # and the labels stay because they are what the audit history reads
            # as (docs/adr/0092 §12, app/workflow/reference_stages.py).
            #
            # Additive and nothing is backfilled: rows written before this
            # carry labels alone and are resolved through the vocabulary, which
            # is the honest reading of what they recorded.
            "from_key": getattr(previous, "key", None),
            "to_key": getattr(stage, "key", None),
        },
    )
    return matter


@transaction.atomic
def change_track(*, matter: Matter, track: str, actor: Any = None) -> Matter:
    if track and track not in Track.values:
        raise DomainError(f"Tundmatu menetlusliik {track!r}.")
    previous = matter.track
    if previous == track:
        return matter

    matter.track = track
    matter.save(update_fields=["track", "updated_at"])
    record_change_event(
        event_type=ChangeEventType.MATTER_TRACK_CHANGED,
        matter=matter,
        actor=actor,
        obj=matter,
        payload={"from": previous, "to": track},
    )
    return matter


def normalize_source_organisations(value: Any) -> list[Any]:
    """Turn whatever a caller offered into a list of distinct Organisations.

    Accepts a single Organisation, any iterable of them, or ``None`` — which
    means *no senders*, the same thing an empty list means. Order is discarded
    on purpose: which sender was ticked first is not a fact about the Matter,
    so two inputs that name the same institutions are the same input (brief 21).
    """
    from app.organisations.models import Organisation

    if value is None:
        return []
    if isinstance(value, Organisation):
        candidates: list[Any] = [value]
    else:
        try:
            candidates = list(value)
        except TypeError as error:
            raise DomainError("Saatjate loend ei ole loetelu.") from error

    seen: dict[Any, Any] = {}
    for candidate in candidates:
        if candidate is None:
            continue
        if not isinstance(candidate, Organisation):
            raise DomainError("Saatja peab olema organisatsioon.")
        if candidate.pk is None:
            raise DomainError("Saatja peab olema salvestatud organisatsioon.")
        seen.setdefault(candidate.pk, candidate)
    return list(seen.values())


def _sender_payload(organisations: Sequence[Any]) -> dict[str, Any]:
    """How a sender set is written into an audit payload.

    Sorted by name, with the primary keys beside the names. The ordering is
    explicit rather than inherited from whatever order the join table handed
    back, so re-reading the same set twice produces the same event body and a
    diff between two events means something actually moved.
    """
    ordered = sorted(
        organisations, key=lambda organisation: (organisation.name, str(organisation.pk))
    )
    return {
        "ids": [str(organisation.pk) for organisation in ordered],
        "names": [organisation.name for organisation in ordered],
    }


def resolve_addressee(*, chosen: Any, typed_name: str) -> Any:
    """The one addressee a Teema form asked for, from a choice and a typed name.

    Both Teema forms — `Uus teema` and `Muuda teemat` — offer the same two ways
    to answer one question, so the precedence between them is written once here
    rather than twice in two views.

    **A typed name wins over the chosen chip.** That is not a preference, it is
    the only rule that makes `Muuda teemat` work: its radio group always carries
    the addressee the Matter already has, so a rule that let the chip win would
    make replacing that addressee by typing a new name impossible — the very
    case this feature exists for. Typing into a field explicitly labelled for a
    new institution is a deliberate act; leaving the previous answer selected is
    not.

    What cannot reach this function is the disclosure's **search box**, which
    posts nothing at all and stays a client-side filter over the choices already
    rendered. That is deliberate: a half-typed «Kliima» left behind after
    somebody selected `Kliimaministeerium` from the filtered list must never
    become an institution of its own.

    Resolution itself is `app.organisations.services.resolve_organisation_name`
    and is not restated here — reuse an exact or alias match, create a genuinely
    new body, refuse an ambiguous spelling. It runs inside the caller's
    transaction, so a Teema save refused afterwards leaves no institution behind.
    """
    from app.organisations.services import resolve_organisation_name

    resolved = resolve_organisation_name(name=typed_name)
    if resolved is not None:
        return resolved
    return chosen


def resolve_source_organisations(*, chosen: Any, typed_name: str) -> list[Any]:
    """The sender set a Teema form asked for, from ticked chips and a typed name.

    **This replaces a rule, not just a control.** Until now the sender field was
    existing-organisations-only, and every sender surface said so:

        Kui saatjat siin ei ole, tuleb asutus enne lisada asutuste alla —
        teema vormilt uut asutust ei teki.

    The decision behind that sentence was that adding an institution is a
    deliberate act on reference data rather than a side effect of filing a
    matter. It is withdrawn, for the reason the addressee side was changed
    first: nobody abandoned a half-filled Teema, navigated to Asutused, created
    a body and came back to find it again. They filed the Teema with no sender,
    and the register lost the fact rather than gaining a considered one
    (docs/adr/0063).

    What is *not* withdrawn is the rule that keeps the catalogue honest, and it
    is not restated here: `resolve_organisation_name` reuses an exact or alias
    match, creates only a genuinely new body, and refuses a spelling that
    already names two. One definition, shared with the addressee field and with
    the closing composer's recipients.

    **The sender relation is plural, so this is a union rather than a
    precedence.** That is the one place it differs from `resolve_addressee`,
    where a typed name has to win because the chip group always carries the
    value the Matter already has and nothing could otherwise replace it. A
    sender does not need replacing — «Euroopa Komisjon» ticked and «Eesti
    Näidisliit» typed is a Matter that arrived from both — so the typed name is
    added. A body reached twice, ticked and then typed, is one sender:
    `resolve_recipients` deduplicates by identity, which is also what
    `MatterSourceOrganisation` enforces.

    Runs inside the caller's transaction. A save refused after this point — a
    rejected attachment, a refused next action — leaves no institution behind.
    """
    from app.organisations.services import resolve_recipients

    return resolve_recipients(
        chosen=list(chosen or []),
        typed_names=[typed_name] if (typed_name or "").strip() else [],
    )


@transaction.atomic
def set_organisations(
    *,
    matter: Matter,
    source_organisations: Any = _UNSET,
    addressee_organisation: Any = _UNSET,
    actor: Any = None,
) -> Matter:
    """Set who the Matter came from and who it is addressed to.

    These are two different facts and are never unified. The register's own
    history is the argument: the counterparty column changed meaning from
    `KELLELT` to `KELLELE` in 2020, so merging them on name similarity would
    silently invert the direction of a decade of records
    (master specification 2.1, 19.3).

    The sender side is a *set* and the addressee side is a single organisation.
    ``_UNSET`` still means "leave this alone" on both, and on the sender side it
    is emphatically not the same as ``[]`` — one is an inline edit of the
    deadline that happened to reach this function, the other is somebody
    clearing the sender list on purpose (Agent-E brief 20).
    """
    changed: dict[str, Any] = {}
    fields: list[str] = []
    new_senders: list[Any] | None = None

    # The Matter first, for the reason `set_policy_areas` gives: `.set()` below
    # refreshes the search projection before the Matter row is saved, and two
    # overlapping saves must queue here rather than on the projection (ENG-029).
    # The comparison reads the locked row, not the caller's instance.
    locked = lock_matter_for_write(matter.pk)

    if source_organisations is not _UNSET:
        proposed = normalize_source_organisations(source_organisations)
        current = list(locked.source_organisations.all())
        if {organisation.pk for organisation in current} != {
            organisation.pk for organisation in proposed
        }:
            changed["source_from"] = _sender_payload(current)
            changed["source_to"] = _sender_payload(proposed)
            new_senders = proposed

    if (
        addressee_organisation is not _UNSET
        and addressee_organisation != locked.addressee_organisation
    ):
        changed["addressee_from"] = getattr(locked.addressee_organisation, "name", None)
        changed["addressee_to"] = getattr(addressee_organisation, "name", None)
        matter.addressee_organisation = addressee_organisation
        fields.append("addressee_organisation")

    if not changed:
        return matter

    if new_senders is not None:
        matter.source_organisations.set(new_senders)
        # `.set()` writes the join table and nothing else, so without this the
        # Matter that just changed would still claim it had not been touched
        # since whenever somebody last edited a scalar field. A sender change is
        # a real edit and every activity surface reads `updated_at`; the save
        # below carries `updated_at` whether or not a scalar field moved
        # (brief 22).

    matter.save(update_fields=[*fields, "updated_at"])
    record_change_event(
        event_type=ChangeEventType.MATTER_ORGANISATION_CHANGED,
        matter=matter,
        actor=actor,
        obj=matter,
        payload=changed,
    )
    return matter


@transaction.atomic
def set_matter_dates(
    *,
    matter: Matter,
    received_date: Any = _UNSET,
    response_deadline: Any = _UNSET,
    actor: Any = None,
) -> Matter:
    changed: dict[str, Any] = {}
    fields: list[str] = []

    if received_date is not _UNSET and received_date != matter.received_date:
        changed["received_from"] = (
            matter.received_date.isoformat() if matter.received_date else None
        )
        changed["received_to"] = received_date.isoformat() if received_date else None
        matter.received_date = received_date
        fields.append("received_date")

    if response_deadline is not _UNSET and response_deadline != matter.response_deadline:
        changed["deadline_from"] = (
            matter.response_deadline.isoformat() if matter.response_deadline else None
        )
        changed["deadline_to"] = response_deadline.isoformat() if response_deadline else None
        matter.response_deadline = response_deadline
        fields.append("response_deadline")

    if not fields:
        return matter

    matter.save(update_fields=[*fields, "updated_at"])
    record_change_event(
        event_type=ChangeEventType.MATTER_DATE_CHANGED,
        matter=matter,
        actor=actor,
        obj=matter,
        payload=changed,
    )
    return matter


@transaction.atomic
def set_position(
    *,
    matter: Matter,
    position_summary: str | None = None,
    rationale_summary: str | None = None,
    actor: Any = None,
) -> Matter:
    """Record Koda's substantive position and the reasoning behind it."""
    fields: list[str] = []
    if position_summary is not None and position_summary != matter.position_summary:
        matter.position_summary = position_summary
        fields.append("position_summary")
    if rationale_summary is not None and rationale_summary != matter.rationale_summary:
        matter.rationale_summary = rationale_summary
        fields.append("rationale_summary")

    if not fields:
        return matter

    matter.save(update_fields=[*fields, "updated_at"])
    record_change_event(
        event_type=ChangeEventType.MATTER_POSITION_UPDATED,
        matter=matter,
        actor=actor,
        obj=matter,
        payload={"fields": fields},
    )
    return matter


@transaction.atomic
def set_brief_summary(*, matter: Matter, value: str, actor: Any = None) -> Matter:
    """Record — or clear — the plain-language `Lühikokkuvõte`.

    The one field the redesign added, and the one thing on the page a formal
    title cannot supply: what this Matter means for the companies affected.

    Audited like every other substantive edit, and audited *without its text*.
    The summary is a working description somebody will rewrite as the file
    develops; copying each version into an audit row would turn the history
    into a second, unmanaged copy of a field whose whole point is that it stays
    current (Teema redesign §6.1).
    """
    cleaned = (value or "").strip()
    if cleaned == matter.brief_summary:
        return matter

    was_empty = not matter.brief_summary
    matter.brief_summary = cleaned
    matter.save(update_fields=["brief_summary", "updated_at"])
    record_change_event(
        event_type=ChangeEventType.MATTER_BRIEF_SUMMARY_SET,
        matter=matter,
        actor=actor,
        obj=matter,
        payload={"created": was_empty and bool(cleaned), "cleared": not cleaned},
    )
    return matter


#: What a stale whole-record save of `Muuda teemat` is told. The sibling of
#: `ENTRY_EDIT_CONFLICT` and `ENGAGEMENT_EDIT_CONFLICT`, and deliberately the
#: same shape of sentence: the page names the record, not the field, because a
#: stale copy of this form is stale about all of them at once.
MATTER_EDIT_CONFLICT = "Teemat on vahepeal mujal muudetud."


class MatterEditConflict(DomainError):
    """The Matter changed elsewhere between rendering an edit form and saving it.

    Carries the row as it now stands, because a conflict a person cannot see
    the other side of is a conflict they cannot resolve — the same reasoning,
    and deliberately the same shape, as :class:`EntryEditConflict`.
    """

    def __init__(self, current: Matter) -> None:
        super().__init__(MATTER_EDIT_CONFLICT)
        self.current = current


def matter_revision_token(matter: Matter) -> str:
    """Which version of a Matter a rendered whole-record form was filled from.

    ``updated_at``, rather than a column of its own — the token
    `entry_revision_token` uses, for the same reasons: `auto_now` sets it on
    every write, PostgreSQL stores it to the microsecond so two saves cannot
    share one, and having it costs no migration.

    **It moves for the facts this form owns and for nothing else.** Every
    service `matter_edit` calls saves the Matter row, including the three that
    write only a join table — `set_organisations` already carried `updated_at`
    for exactly this reason, and `set_policy_areas` and `set_legal_instruments`
    now do too. A `Märge`, an opinion or a file does *not* touch it, so adding
    content to a Matter never makes somebody's open edit page stale. That is
    the granularity a whole-record token wants: guard the record the form
    posts, and leave the rest of the file alone.
    """
    return matter.updated_at.isoformat()


def guard_matter_revision(*, matter: Matter, expected_revision: str | None) -> Matter:
    """Lock the Matter and refuse a whole-record save filled from an older one.

    The same discipline as `edit_entry`: take the row, then read the version
    *from the locked row* rather than from the instance the caller arrived
    with, so the comparison is against what is committed rather than against
    what was on screen. Must be called inside `transaction.atomic`.

    ``FOR NO KEY UPDATE`` — the same row at the same strength as
    `lock_matter_for_evidence_integrity` and `lock_open_matter_for_business_write`
    take it, so this adds no new lock and does not change the global order
    (`app/matters/locks.py`).

    An absent ``expected_revision`` is not a conflict. A caller that has no
    token is a caller that never rendered one — an inline header control, a
    service call, a test — and refusing those would make the guard a rule about
    who may write rather than about which version they wrote against.
    """
    locked = Matter.objects.select_for_update(no_key=True).get(pk=matter.pk)
    if expected_revision and matter_revision_token(locked) != expected_revision:
        raise MatterEditConflict(locked)
    return locked


#: What a stale inline save of one whole-value field is told (ENG-028). The
#: inline editors each replace one value whole, so a copy rendered before a
#: colleague's save is stale about exactly that value and nothing else.
MATTER_FIELD_CONFLICT = (
    "Seda välja on vahepeal mujal muudetud. Salvestatud väärtus on nüüd näha — "
    "vaata see üle ja salvesta uuesti."
)

#: The inline editors that replace a whole value and carry a revision of it.
GUARDED_MATTER_FIELDS = frozenset({"policy_areas", "source_organisations", "brief_summary"})


class MatterFieldConflict(MatterEditConflict):
    """One inline whole-value field changed elsewhere since it was rendered.

    A `MatterEditConflict`, so everything that already answers a stale
    `Muuda teemat` save with 409 recognises this one, and it carries the row as
    it now stands for the same reason.
    """

    def __init__(self, current: Matter, field: str) -> None:
        DomainError.__init__(self, MATTER_FIELD_CONFLICT)
        self.current = current
        self.field = field


def matter_field_revision(matter: Matter, field: str) -> str:
    """A digest of the one value an inline editor displays and replaces.

    **Scoped to the field, not the Matter** (ENG-028). ADR 0104's token for
    `Muuda teemat` is `updated_at`, because that form posts every fact at once.
    The inline editors are three separate fragments — Valdkonnad in the header,
    Saatja in the rail, Lühikokkuvõte under the meta line — and each re-renders
    only itself after a save. A token that moved on every Matter write would
    make the Saatja box stale the moment the same person saved Valdkonnad, and
    refuse them in their own tab. What a whole-value editor must not do is
    overwrite a *different value of its own field* than the one it showed, so
    that value is what the token is.

    Read with fresh queries rather than through the instance's caches, so the
    digest describes the database and not a prefetch.
    """
    if field == "policy_areas":
        parts = sorted(str(pk) for pk in matter.policy_areas.values_list("pk", flat=True))
    elif field == "source_organisations":
        parts = sorted(str(pk) for pk in matter.source_organisations.values_list("pk", flat=True))
    elif field == "brief_summary":
        parts = [
            Matter.objects.filter(pk=matter.pk).values_list("brief_summary", flat=True).first()
            or ""
        ]
    else:  # pragma: no cover - a programming error, not a request
        raise ValueError(f"no revision for {field!r}")
    material = "\x1f".join([field, *parts]).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:32]


def guard_matter_field_revision(*, matter: Matter, field: str, expected: str | None) -> Matter:
    """Lock the Matter and refuse an inline save of a value that moved since.

    The Matter row at `FOR NO KEY UPDATE` — the same lock the set editors take
    for their search refresh (ENG-029), so one lock both decides whether the
    save is stale and serialises it against the colleague it might overwrite.
    Must be called inside `transaction.atomic`, before anything is written.

    **A missing token is a conflict here**, unlike `guard_matter_revision`.
    These forms always render one, so a POST without it came from a page older
    than the guard or from something that is not the page; accepting it would
    make the guard optional in exactly the case it exists for.
    """
    locked = lock_matter_for_write(matter.pk)
    if not expected or matter_field_revision(locked, field) != expected:
        raise MatterFieldConflict(locked, field)
    return locked


@transaction.atomic
def set_matter_title(*, matter: Matter, value: str, actor: Any = None) -> Matter:
    """Rename a Matter.

    Editable, and deliberately not editable inline in the header. A rename is
    the one change most likely to make a colleague think they are looking at a
    different file, so it belongs on the edit page beside the rest of the
    record, where somebody is already deciding what this Matter *is*.

    The title is required and stays required: a Matter with no name cannot be
    found, cited or handed over, and `create_matter` refuses one for the same
    reason (specification 3.8). Trimmed and capped exactly as creation trims and
    caps it, in one place, because two callers normalising a string two ways is
    how the same value starts comparing unequal to itself.

    Unlike `Lühikokkuvõte`, the audit payload carries both strings. The summary
    is a working description somebody rewrites as the file develops; the title
    is the handle everything else refers to, and the old one is how a person
    finds a Matter again after it stopped being called what they remember
    (Teema QA §2.4).

    What this does **not** touch: the reference, the register identity, the
    origin, or any imported provenance. A rename is a rename.
    """
    cleaned = (value or "").strip()
    if not cleaned:
        raise DomainError("Teemal peab olema pealkiri.")
    cleaned = cleaned[:1000]
    if cleaned == matter.title:
        return matter

    previous = matter.title
    matter.title = cleaned
    matter.save(update_fields=["title", "updated_at"])
    record_change_event(
        event_type=ChangeEventType.MATTER_TITLE_CHANGED,
        matter=matter,
        actor=actor,
        obj=matter,
        summary=cleaned[:200],
        payload={"previous": previous, "current": cleaned},
    )
    return matter


@transaction.atomic
def set_tags(*, matter: Matter, tags: Sequence[Any], actor: Any = None) -> Matter:
    """Replace the Matter's Sildid with the chosen set.

    The set is replaced whole rather than diffed, like `set_policy_areas`,
    because that is what a checkbox list posts: an unticked box is simply
    absent.

    Two things this keeps that a plain `.set()` would quietly destroy.

    **Provenance.** A `TagAssignment` records *how* a tag got there — by hand,
    from the import, or from an approved rule — and who confirmed it. Rewriting
    the whole set every save would restamp an imported assignment as manual and
    lose the day somebody confirmed it, so assignments that survive the edit are
    left completely alone; only genuine additions and removals are written.

    **The one-event-per-change rule.** Adding two tags and removing one is three
    facts, and the timeline records three: `TAG_ASSIGNED` and `TAG_REMOVED`
    already exist for exactly this and there is no combined event to invent.

    Nothing here creates a `Tag`. The vocabulary is governed elsewhere and an
    edit page is not where new taxonomy gets invented (master specification
    11.2, 21.2).
    """
    # The Matter first, like every other set on this record: each assignment
    # written below refreshes the search projection (ENG-029).
    lock_matter_for_write(matter.pk)
    chosen = {tag.pk: tag for tag in tags}
    existing = {
        assignment.tag_id: assignment
        for assignment in TagAssignment.objects.filter(matter=matter).select_related("tag")
    }

    for tag_id, assignment in existing.items():
        if tag_id in chosen:
            continue
        name = assignment.tag.name_et
        assignment.delete()
        record_change_event(
            event_type=ChangeEventType.TAG_REMOVED,
            matter=matter,
            actor=actor,
            obj=matter,
            summary=name[:200],
            payload={"tag": str(tag_id)},
        )

    for tag_id, tag in chosen.items():
        if tag_id in existing:
            continue
        TagAssignment.objects.create(
            matter=matter,
            tag=tag,
            source=TagAssignmentSource.MANUAL,
            confirmed_by=actor if getattr(actor, "is_authenticated", False) else None,
            confirmed_at=timezone.now(),
        )
        record_change_event(
            event_type=ChangeEventType.TAG_ASSIGNED,
            matter=matter,
            actor=actor,
            obj=matter,
            summary=tag.name_et[:200],
            payload={"tag": str(tag_id)},
        )
    return matter


def personal_note_for(*, matter: Matter, author: Any) -> str:
    """One person's private note on one Matter, or an empty string.

    Read by user, never by visibility: there is no product surface that shows a
    colleague's notes, so there is no reader here but the author
    (app/matters/models.py, `MatterPersonalNote`).
    """
    record = personal_note_record(matter=matter, author=author)
    return record.body if record is not None else ""


def personal_note_record(*, matter: Matter, author: Any) -> MatterPersonalNote | None:
    """The row itself, for a caller that needs its body **and** when it was saved.

    The Teema rail needs both — the text goes in the box, `updated_at` goes in
    `Salvestatud HH:mm` — and reading them through two helpers would ask the same
    table the same question twice per page render (docs/adr/0074 §18).
    """
    if author is None or not getattr(author, "is_authenticated", False):
        return None
    return MatterPersonalNote.objects.filter(matter=matter, author=author).first()


#: What the reader was told when they were handed the box.
#:
#: One sentence, and it says what happened rather than what to do about it —
#: their own text is still in the textarea and the newer version is under it, so
#: the choice is theirs to make with both in front of them.
PERSONAL_NOTE_CONFLICT = "Märget on muudetud teises aknas. Sinu muudatust ei salvestatud."


class PersonalNoteConflict(DomainError):
    """The note changed elsewhere between rendering this box and saving it.

    Carries the row as it now stands, because a conflict a person cannot see the
    other side of is a conflict they cannot resolve.
    """

    def __init__(self, current: MatterPersonalNote) -> None:
        super().__init__(PERSONAL_NOTE_CONFLICT)
        self.current = current


def personal_note_revision(record: MatterPersonalNote | None) -> str:
    """Which version of the note a rendered box was filled from.

    `updated_at`, rather than a column of its own. It is set by `auto_now` on
    every write, it is already what the rail prints as `Salvestatud HH:mm`, and
    PostgreSQL stores it to the microsecond — so two saves cannot share a token
    and no migration is needed to have one. A note that has never been saved has
    no version, and the empty string says so.
    """
    return record.updated_at.isoformat() if record is not None else ""


def save_personal_note(
    *, matter: Matter, author: Any, body: str, expected_revision: str | None = None
) -> MatterPersonalNote:
    """Autosave a private draft.

    Writes no `ChangeEvent` on purpose, and is the only write in the product
    that does not. It is not a business change: nothing downstream reads it, no
    statistic counts it, it never appears on the timeline and it is not
    evidence. Recording every autosave of somebody's scratch paper as
    authoritative history would bury the history it sits beside
    (Teema redesign §22.4).

    **Optimistic concurrency** (adversarial QA 2026-09-12, QA-09). Two tabs on
    one Teema is the ordinary way a lawyer works. Tab A wrote a note and saved
    it; tab B, still holding the text as it was before that, autosaved 900 ms
    after its own next keystroke — and last-write-wins overwrote A silently. No
    warning, no copy kept, and nothing anywhere from which to recover it: this
    is the only write in the product that files no history, so the overwritten
    version was simply gone. That is persisted data loss, and it is the one
    thing an autosave must not be able to do.

    So a caller that knows which version its box was filled from says so, and a
    save whose ``expected_revision`` is not the stored one raises
    :class:`PersonalNoteConflict` and **writes nothing**. What the person typed
    is theirs to keep; what the other tab saved is theirs to read; which one wins
    is not this function's decision to make, and it does not silently retry with
    the newer token either.

    ``expected_revision=None`` means «no opinion», and is not an unchecked
    door: it is for the caller that is creating a Matter and its first note in
    one act, where there is no earlier version for a second tab to be holding.
    """
    if author is None or not getattr(author, "is_authenticated", False):
        raise DomainError("Märkmeid saab salvestada ainult sisselogitud kasutaja.")
    with transaction.atomic():
        # `no_key=True` throughout this module: a plain `FOR UPDATE` on a row
        # other transactions reference by foreign key is how the two deadlock
        # cycles in this codebase were built (app/matters/locks.py).
        locked = (
            MatterPersonalNote.objects.select_for_update(no_key=True)
            .filter(matter=matter, author=author)
            .first()
        )
        if expected_revision is not None and personal_note_revision(locked) != expected_revision:
            # Read *after* the lock, so the version compared against is the one
            # that is committed rather than the one that was on screen.
            raise PersonalNoteConflict(locked or MatterPersonalNote(matter=matter, author=author))
        if locked is None:
            return MatterPersonalNote.objects.create(matter=matter, author=author, body=body or "")
        locked.body = body or ""
        locked.save(update_fields=["body", "updated_at"])
        return locked


@transaction.atomic
def set_policy_areas(*, matter: Matter, policy_areas: Sequence[Any], actor: Any = None) -> Matter:
    """Replace the Matter's Valdkonnad with the chosen set.

    Inline from the Teema header, so filing a Matter correctly no longer means
    opening an edit page. The set is replaced whole rather than diffed, because
    that is what the control posts: an unticked checkbox is simply absent, so
    "none of them" and "this POST is about something else" would otherwise be
    indistinguishable — which is why the endpoint names the field in its URL.

    Retired areas are not removed here. A Matter filed years ago under
    `Halduskoormus` keeps it, because the header's control is seeded from the
    Matter's *current* areas plus the offered vocabulary, and unticking one is a
    decision somebody makes deliberately (Teema redesign §7.2).
    """
    # **The Matter first, then the set** (ENG-029). `.set()` fires the search
    # refresh from inside the join-table write, *before* the Matter row is
    # saved, so without this lock the refresh reached `SearchDocument` first:
    # two overlapping saves of one Teema then locked the projection row and the
    # Matter row in opposite orders and deadlocked, or both deleted the same
    # projection and one re-insert hit its unique index. Locked here, every
    # writer of this Teema queues on its row before touching anything else,
    # which is the order every scalar setter already had (app/matters/locks.py).
    #
    # And `before` is read from the locked row rather than from the caller's
    # instance, whose `policy_areas` may be a prefetch taken before the wait.
    locked = lock_matter_for_write(matter.pk)
    chosen = list(policy_areas)
    before = {area.pk for area in locked.policy_areas.all()}
    after = {area.pk for area in chosen}
    if before == after:
        return matter

    matter.policy_areas.set(chosen)
    # `.set()` writes the join table and nothing else, so without this the
    # Matter that just changed would still claim it had not been touched since
    # whenever somebody last edited a scalar field. `set_organisations` has
    # carried `updated_at` for that reason since brief 22; a whole-record
    # revision token makes it load-bearing here too, because a stale copy of
    # `Muuda teemat` must not be accepted as current after somebody else
    # changed exactly these (`matter_revision_token`).
    matter.save(update_fields=["updated_at"])
    record_change_event(
        event_type=ChangeEventType.MATTER_POLICY_AREAS_CHANGED,
        matter=matter,
        actor=actor,
        obj=matter,
        summary=", ".join(area.name_et for area in chosen)[:200],
        payload={
            "added": sorted(str(pk) for pk in after - before),
            "removed": sorted(str(pk) for pk in before - after),
        },
    )
    return matter


@transaction.atomic
def set_policy_area_other(*, matter: Matter, value: str, actor: Any = None) -> Matter:
    """Record — or clear — the free-text area beside the canonical ones.

    The counterpart to what `create_matter` accepts, so a Matter filed under
    "Muu" on the day it arrived is not stuck with whatever was typed then. Same
    trimming and same length cap, in one place, because two callers normalising
    a string two ways is how the same value starts comparing unequal to itself.

    It stays free text. Nothing here creates a `PolicyArea`, nothing creates a
    `Tag`, and no statistic counts it (Stage-2E.1 brief 20).
    """
    cleaned = (value or "").strip()[:400]
    if cleaned == matter.policy_area_other:
        return matter

    matter.policy_area_other = cleaned
    matter.save(update_fields=["policy_area_other", "updated_at"])
    record_change_event(
        event_type=ChangeEventType.MATTER_POLICY_AREA_OTHER_SET,
        matter=matter,
        actor=actor,
        obj=matter,
        # The value itself is not in the payload. A timeline entry that quotes
        # the old and new text turns an audit row into a second, unmanaged copy
        # of a field somebody may later have had a reason to clear.
        payload={"cleared": not cleaned},
    )
    return matter


@transaction.atomic
def set_legal_instruments(
    *, matter: Matter, legal_instruments: Sequence[Any], actor: Any = None
) -> Matter:
    """Replace the Matter's Õigusakt with the chosen set.

    The counterpart to what `create_matter` accepts, so a canonical fact
    recorded on the day a file arrived is not stuck that way. Everything about
    the shape mirrors `set_policy_areas` above, deliberately: the set is
    replaced whole rather than diffed, because an unticked checkbox is simply
    absent from a POST and "none of them" and "this request is about something
    else" would otherwise be indistinguishable.

    **A view never writes this relation directly.** The whole reason this
    function exists rather than an `matter.legal_instruments.set(...)` in the
    edit view is the change event below: a canonical classification that moved
    without one is a correction the audit trail cannot answer for (task §19).

    Returns early when nothing moved, so a save that changed a title writes one
    event rather than two.
    """
    chosen = list(legal_instruments)
    before = {item.pk for item in matter.legal_instruments.all()}
    after = {item.pk for item in chosen}
    if before == after:
        return matter

    matter.legal_instruments.set(chosen)
    # `.set()` writes the join table and nothing else, so without this the
    # Matter that just changed would still claim it had not been touched since
    # whenever somebody last edited a scalar field. `set_organisations` has
    # carried `updated_at` for that reason since brief 22; a whole-record
    # revision token makes it load-bearing here too, because a stale copy of
    # `Muuda teemat` must not be accepted as current after somebody else
    # changed exactly these (`matter_revision_token`).
    matter.save(update_fields=["updated_at"])
    record_change_event(
        event_type=ChangeEventType.MATTER_LEGAL_INSTRUMENTS_CHANGED,
        matter=matter,
        actor=actor,
        obj=matter,
        summary=", ".join(item.label_et for item in chosen)[:200],
        payload={
            "added": sorted(str(pk) for pk in after - before),
            "removed": sorted(str(pk) for pk in before - after),
        },
    )
    return matter


@transaction.atomic
def set_legal_instrument_other(*, matter: Matter, value: str, actor: Any = None) -> Matter:
    """Record — or clear — the free text beside the canonical instruments.

    Same trimming and same length cap as `create_matter` applies, in one place,
    because two callers normalising a string two ways is how the same value
    starts comparing unequal to itself.

    It stays free text. Nothing here creates a `LegalInstrumentType` and no
    statistic counts it as one — the rule `set_policy_area_other` follows, for
    the same reason: a taxonomy that grows from whatever somebody typed is a
    taxonomy that stops being reviewed (docs/adr/0070).
    """
    cleaned = (value or "").strip()[:400]
    if cleaned == matter.legal_instrument_other:
        return matter

    matter.legal_instrument_other = cleaned
    matter.save(update_fields=["legal_instrument_other", "updated_at"])
    record_change_event(
        event_type=ChangeEventType.MATTER_LEGAL_INSTRUMENT_OTHER_SET,
        matter=matter,
        actor=actor,
        obj=matter,
        # The value itself is not in the payload, for the reason
        # `set_policy_area_other` keeps it out: a timeline entry that quotes the
        # old and new text becomes a second, unmanaged copy of a field somebody
        # may later have had a reason to clear.
        payload={"cleared": not cleaned},
    )
    return matter


@transaction.atomic
def add_source_derived_policy_areas(
    *,
    matter: Matter,
    policy_areas: Sequence[Any],
    actor: Any = None,
    provenance: dict[str, Any] | None = None,
) -> list[Any]:
    """Add canonical areas a reviewed source mapping proposed. Never removes.

    The one write path for classification derived from imported evidence, and
    deliberately narrow. It exists because the OneNote enrichment would
    otherwise reach through ``matter.policy_areas`` from a management command,
    which is the one place in this codebase where a business change would happen
    with no named use case and no audit row behind it.

    **Additive, always.** The modern taxonomy and the way the department filed
    things in OneNote are not the same classification, and a page that lived in
    one drawer is not evidence that a lawyer's own choice was wrong. An area
    somebody set by hand survives this untouched (Agent-G brief 45).

    **It creates no taxonomy.** The caller resolves a mapping to an existing
    ``PolicyArea`` or reports a configuration error; a source section that
    matches nothing stays unmapped, which is a valid answer (brief 46).

    Recorded as ``IMPORT_APPLIED`` — the event this codebase already uses for
    "an import wrote something onto this Matter" — with the mapping's identity
    in the payload. A new event type would mean an audit migration for
    vocabulary, and this one is truthful: an import applied it, and the
    provenance says which. It is not in ``TIMELINE_EVENT_TYPES``, so filing does
    not push meeting notes out of the professional narrative.

    Adding nothing raises nothing. That is what makes a second apply a no-op in
    the audit trail as well as in the data.
    """
    existing = set(matter.policy_areas.values_list("pk", flat=True))
    missing: list[Any] = []
    for area in policy_areas:
        # Deduplicated as well as filtered. Two accepted source pages filed in
        # the same place propose the same area twice, and counting that as two
        # additions would report a number the database never held.
        if area.pk in existing:
            continue
        existing.add(area.pk)
        missing.append(area)
    if not missing:
        return []

    matter.policy_areas.add(*missing)
    payload: dict[str, Any] = {"policy_area_keys": sorted(area.key for area in missing)}
    if provenance:
        payload["provenance"] = provenance
    record_change_event(
        event_type=ChangeEventType.IMPORT_APPLIED,
        matter=matter,
        actor=actor,
        obj=matter,
        summary=", ".join(sorted(area.name_et for area in missing))[:200],
        payload=payload,
    )
    return missing


#: The only schemes an engagement link may use.
#:
#: Not a general URL policy — a narrow allow-list for one field that renders as
#: a clickable control on a page a lawyer trusts. `javascript:` and `data:` are
#: script delivery dressed as an address; `file:` and `ftp:` point somewhere the
#: reader's browser cannot usefully follow. Nothing here fetches the link, and
#: nothing checks whether the far end is alive: an engagement recorded in 2019
#: whose campaign has since been archived is still a true record of what the
#: Chamber did (Agent-F brief 12).
ENGAGEMENT_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})


def _normalize_public_link(
    value: str | None,
    *,
    max_length: int,
    reject_credentials: bool = False,
    not_a_url: str = "Link peab sisaldama veebiaadressi.",
    not_web_scheme: str = "Link peab algama http:// või https:// aadressiga.",
    has_credentials: str = "Link ei tohi sisaldada kasutajanime ega parooli.",
    too_long: str | None = None,
) -> str:
    """One implementation of «a public http(s) address, or nothing».

    Factored out of :func:`normalize_engagement_url` when `Väline seisukoht`
    needed exactly the same rule under its own column width, because the rule is
    not the kind that may exist twice: it is the difference between a clickable
    control on a page a lawyer trusts and a script-delivery vector, and a second
    copy is a second place for `javascript:` to be forgotten. ``max_length`` is
    the only thing the first two callers disagreed about, and both of them state
    their own — the refusal sentence is built from it, so neither caller's
    wording changed when this was extracted.

    The refusal sentences are parameters for the third caller,
    :func:`normalize_overview_news_url`, which names its own record in every one
    of them. A shared «Link peab sisaldama veebiaadressi.» would have been the
    only sentence on a Teema page that did not say *which* link it meant, on a
    page that now renders three different kinds of them.

    ``reject_credentials`` is off by default and the default is the older
    behaviour, deliberately. `https://user:pw@host/` has a perfectly good host,
    and for a campaign address out of the historical register — which this
    department did not choose and cannot re-issue — refusing it would mean
    refusing to record what actually happened. An `Ülevaade / uudis` address is
    one somebody is pasting *now*, from a page they have open, so there is no
    such history to accommodate and a credential on the file is a credential in
    an audit payload, on a rendered page and in everybody's browser history
    (docs/adr/0081 §3, kept by docs/adr/0085 §2).
    """
    from urllib.parse import urlsplit

    url = (value or "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        raise DomainError(not_a_url) from None
    if parts.scheme.lower() not in ENGAGEMENT_URL_SCHEMES:
        raise DomainError(not_web_scheme)
    try:
        hostname = parts.hostname
        username = parts.username
        password = parts.password
    except ValueError:
        # A malformed authority — an unbracketed IPv6 literal, a port that is
        # not a number. A refusal, not an unhandled exception from a parser.
        hostname = None
        username = password = None
    if reject_credentials and (username or password):
        raise DomainError(has_credentials)
    if not hostname:
        raise DomainError(not_a_url)
    if len(url) > max_length:
        raise DomainError(
            too_long
            or (
                f"Link on liiga pikk — kuni {max_length} tähemärki. "
                "Lühenda aadressi või salvesta see märkusesse."
            )
        )
    return url


def normalize_engagement_url(value: str | None) -> str:
    """Trim it, allow it to be empty, refuse anything not http(s), bound it.

    The one door every writer of the three engagement link columns passes
    through — `add_engagement`, `update_engagement`, the composer, the
    correction form, and `app.legacy_import.register_outreach` — so it is where
    the column's own width has to be enforced.

    **Refused, never truncated** (red-team finding F-1). A link cut off at a
    thousand characters is a link that no longer resolves, and storing one
    leaves a reader following a dead address in the belief that it is what was
    recorded — a stored pointer that is quietly wrong is worse than a refusal
    naming the row. Without this the value reached PostgreSQL, which answered
    `StringDataRightTruncation`: a `DataError` with no row reference that aborts
    the surrounding transaction, so an importer lost a whole batch to one long
    URL and a route answered 500 where every other bad link on it answers 400.

    **A host, not merely an authority.** `https://user:pw@/path` has a
    non-empty `netloc` and no host at all, which is not an address anybody can
    follow and is the one shape whose «host» is nothing but credentials
    (red-team finding F-2, and `MatterEngagement._hostname` is its other half).
    """
    return _normalize_public_link(value, max_length=ENGAGEMENT_URL_MAX_LENGTH)


def normalize_external_position_url(value: str | None) -> str:
    """The same rule, for the address a `Väline seisukoht` points at.

    Its own function rather than a call to `normalize_engagement_url` at every
    site, because the two are different product decisions that agree today: an
    engagement link is a pointer to whatever campaign tool Koda used, and this
    one is somebody else's published page. What they must never disagree about
    is the safety half — `http` and `https` only, a parsed host rather than a
    substring, refused rather than truncated — and that half is shared rather
    than copied (docs/adr/0084 §3).

    **No host allow-list**, deliberately, and for the reason
    `normalize_overview_news_url` now gives as well: this records where another
    organisation published its position, and the set of those is every
    institution in Estonia and the EU. A list would be a list somebody has to
    maintain, and the day a ministry moves domain the file would refuse to
    record what actually happened. The one rule the two do *not* share is
    userinfo, which this one tolerates and that one refuses — an `Ülevaade /
    uudis` address is pasted from a page somebody has open, where a position's
    may come out of a historical register nobody can re-issue.

    Empty is not a refusal here either. A position may carry a document and no
    address at all, and which of the two it has is decided by
    :func:`record_external_position`, where both halves are visible.
    """
    return _normalize_public_link(value, max_length=EXTERNAL_POSITION_URL_MAX_LENGTH)


def _engagement_kind(value: str) -> str:
    if value not in EngagementKind.values:
        raise DomainError(f"Tundmatu kaasamise liik {value!r}.")
    return value


def _engagement_precision(occurred_on: Any, value: Any) -> str:
    """How exactly `Kaasamise kuupäev` is known, normalised and vouched for.

    Two rules, and both are about a value a form or an importer can get wrong.

    **A date nobody knows has no precision.** `NULL` + `MONTH` is not «an
    approximate consultation», it is a period with nothing to qualify — and
    every surface reading it would have to guess whether to print a period or
    «kuupäev teadmata». So a missing date forces `EXACT`, which is what the
    column holds for every undated row today.

    **The vocabulary is checked here rather than by the database.** The
    `CheckConstraint` is the backstop; a `DomainError` from the service names
    what was wrong, where an `IntegrityError` out of a composer transaction
    that has already written a note and a next step names a constraint. Same
    reasoning as `_engagement_kind` and `_engagement_response_count`.
    """
    if occurred_on is None:
        return DatePrecision.EXACT.value
    precision = value or DatePrecision.EXACT.value
    if precision not in DatePrecision.values:
        raise DomainError(f"Tundmatu kuupäeva täpsus {precision!r}.")
    return precision


@transaction.atomic
def _engagement_response_count(value: Any) -> int | None:
    """`Vastuseid`, or nothing at all.

    ``None`` passes through: not answering is the ordinary case and it is a
    different fact from answering zero. Anything else has to be a whole
    non-negative number, because the column is one and a refusal here is far
    better than an `IntegrityError` from inside a composer transaction that has
    already written a note and a next step (docs/adr/0074 §5).
    """
    if value is None:
        return None
    try:
        count = int(value)
    except (TypeError, ValueError):
        raise DomainError("Vastuste arv peab olema täisarv.") from None
    if count < 0:
        raise DomainError("Vastuste arv ei saa olla negatiivne.")
    return count


@transaction.atomic
def record_engagement(
    *,
    matter: Matter,
    kind: str,
    title: str,
    url: str = "",
    smaily_url: str = "",
    alchemer_url: str = "",
    note: str = "",
    occurred_on: Any = None,
    occurred_on_precision: str = DatePrecision.EXACT.value,
    feedback_deadline: Any = None,
    feedback_received: str = "",
    response_count: Any = None,
    actor: Any = None,
) -> MatterEngagement:
    """`Kaasamine`, recorded by a person on a Matter that is open. The UI's door.

    A thin use case over :func:`add_engagement`, and it exists for one reason:
    the leaf below has a second, legitimate writer that must *not* be held to
    this rule. `app.legacy_import.register_outreach` files consultations onto
    imported Matters, and most of those are closed — the register is full of
    finished work, and refusing to record what was already done to it would
    break the import rather than protect anything.

    So the closed-Matter rule is stated where a *person* writes, not where the
    row is created (R2-02). The Teema overview's `Kaasamine` form posts here;
    the workspace's `+ Kaasamine` takes the same lock itself in
    `app/matters/workspace.py`; the importer keeps the leaf.

    ``feedback_deadline`` is passed straight through, like every other field.
    It was the one column this door could not carry: `add_engagement` has taken
    it since docs/adr/0078 §3 and this function did not, so a save arriving
    here wrote `NULL` however carefully the form had been filled in. A door
    that silently drops a field is worse than one that does not offer it.
    """
    locked = lock_open_matter_for_business_write(matter.pk)
    return add_engagement(
        matter=locked,
        kind=kind,
        title=title,
        url=url,
        smaily_url=smaily_url,
        alchemer_url=alchemer_url,
        note=note,
        occurred_on=occurred_on,
        occurred_on_precision=occurred_on_precision,
        feedback_deadline=feedback_deadline,
        feedback_received=feedback_received,
        response_count=response_count,
        actor=actor,
    )


def add_engagement(
    *,
    matter: Matter,
    kind: str,
    title: str,
    url: str = "",
    smaily_url: str = "",
    alchemer_url: str = "",
    note: str = "",
    occurred_on: Any = None,
    occurred_on_precision: str = DatePrecision.EXACT.value,
    feedback_deadline: Any = None,
    feedback_received: str = "",
    response_count: Any = None,
    actor: Any = None,
) -> MatterEngagement:
    """Record one act of asking members or stakeholders for input.

    Writes no `Entry`. One action must not become two records — a structured
    engagement and a narrative note saying the same thing — because the day they
    disagree there is no way to tell which was meant (brief 45).

    ``title`` is what the approved Teema target asks as `Keda kaasati`, and what
    the older five-field form asked as `Pealkiri`. One column, one meaning — the
    line that identifies this engagement to a reader — and the question printed
    above it is the surface's to choose (docs/adr/0074 §4).

    ``smaily_url`` and ``alchemer_url`` are pointers and nothing more. They go
    through the same scheme allow-list as ``url``, nothing here contacts either
    provider, and a link on its own has never been enough to make an
    engagement: ``title`` is still required, so a row cannot come into
    existence as two addresses and no statement of who was engaged
    (docs/adr/0027, amended 2026-09-12).

    ``feedback_deadline`` is `Tagasisidet ootame kuni` — the day the people who
    were asked were told to answer by. Optional, stored exactly as given, and
    never derived: a caller that does not name it writes ``NULL`` rather than
    borrowing ``occurred_on`` or today. **It stays an exact day**: docs/adr/0082
    widened `Kaasamise kuupäev` and deliberately left this one on
    docs/adr/0079 §11's list, because a reply-by date is a day somebody named to
    other people.

    Naming it **opens a feedback wait**, and the wait is work until somebody
    finishes it (:func:`complete_engagement_feedback`). What that does *not* do
    is create anything: no `NextAction`, no `MatterImportantDate`, no
    `Matter.response_deadline`, no deadline row and no count. The wait is a
    *reading* of this record by `app/matters/work_items.py`, which is why
    closing it is one column on this table rather than a state machine
    somewhere else (docs/adr/0086 §3).

    ``feedback_received`` is `Saadud tagasiside / arvamused`, and it is
    independent of the deadline: a round recorded after the fact may arrive
    with the answers already written down and no wait ever opened. Nothing is
    completed here — a row created carrying feedback and a deadline is a wait
    that is open and already has something in it, because writing down what
    came back and deciding the round is over are two acts and only the second
    one is a decision (docs/adr/0086 §6).

    ``occurred_on_precision`` says how exactly ``occurred_on`` is known, and
    ``occurred_on`` is then the **anchor** of that period — the normalisation
    every surface goes through is `app.workflow.dates.bounds_for`, so a quarter
    stated in `+ Kaasamine` is the same stored value as a quarter stated
    anywhere else. `EXACT` by default, which is what a caller that knows
    nothing about precision means and what every historical row holds.

    **An unknown date is normalised back to `EXACT`.** A `NULL` date with a
    stored `MONTH` beside it would be a period with nothing to qualify — a row
    that renders as neither a date nor «kuupäev teadmata» but as whichever of
    the two the reading surface guessed. Absence has no precision.
    """
    clean_title = title.strip()
    if not clean_title:
        raise DomainError("Kaasamisel peab olema pealkiri.")
    precision = _engagement_precision(occurred_on, occurred_on_precision)

    engagement = MatterEngagement.objects.create(
        matter=matter,
        kind=_engagement_kind(kind),
        title=clean_title[:500],
        url=normalize_engagement_url(url),
        smaily_url=normalize_engagement_url(smaily_url),
        alchemer_url=normalize_engagement_url(alchemer_url),
        note=note.strip(),
        occurred_on=occurred_on,
        occurred_on_precision=precision,
        feedback_deadline=feedback_deadline,
        feedback_received=(feedback_received or "").strip(),
        response_count=_engagement_response_count(response_count),
        created_by=actor,
    )
    record_change_event(
        event_type=ChangeEventType.ENGAGEMENT_ADDED,
        matter=matter,
        actor=actor,
        obj=engagement,
        summary=engagement.title[:200],
        payload={
            "kind": engagement.kind,
            "occurred_on": engagement.occurred_on.isoformat() if engagement.occurred_on else None,
            # The precision beside the value, never on its own. The stored date
            # is a period's first day, so an audit row carrying `2026-10-01`
            # with nothing else says «1 October» to whoever reads it back,
            # which is the invention docs/adr/0079 §2 exists to refuse.
            "occurred_on_precision": engagement.occurred_on_precision,
            # The date itself, like `occurred_on` beside it: it is a small
            # value, it is the thing a correction would change, and an audit
            # row saying only «a deadline was set» cannot answer «to when».
            "feedback_deadline": (
                engagement.feedback_deadline.isoformat() if engagement.feedback_deadline else None
            ),
            # Whether anything came back in writing, not what it said. The
            # words are on the record where they can be corrected; an audit
            # table holding a second copy of them would be a worse copy nobody
            # maintains (Agent-F brief 26).
            "has_feedback_received": bool(engagement.feedback_received),
            "has_url": bool(engagement.url),
            "has_smaily_url": bool(engagement.smaily_url),
            "has_alchemer_url": bool(engagement.alchemer_url),
            # Whether it was counted, not what the count was. The number is on
            # the record where a reader can correct it; the audit row says a
            # question was answered (brief 26).
            "has_response_count": engagement.response_count is not None,
        },
    )
    return engagement


#: What a stale correction is told, in one place because a view, a template and
#: a test all have to agree about it. The sibling of `ENTRY_EDIT_CONFLICT`, and
#: deliberately the same shape of sentence.
ENGAGEMENT_EDIT_CONFLICT = "Kaasamist on vahepeal mujal muudetud."


class EngagementEditConflict(DomainError):
    """The engagement changed elsewhere between rendering a form and saving it.

    Carries the row as it now stands, because a conflict a person cannot see
    the other side of is a conflict they cannot resolve — the same reasoning,
    and deliberately the same shape, as :class:`EntryEditConflict`.
    """

    def __init__(self, current: MatterEngagement) -> None:
        super().__init__(ENGAGEMENT_EDIT_CONFLICT)
        self.current = current


def engagement_revision_token(engagement: MatterEngagement) -> str:
    """Which version of an engagement a rendered correction form was filled from.

    ``updated_at``, for the reasons `entry_revision_token` gives: `auto_now`
    sets it on every write, PostgreSQL stores it to the microsecond so two
    saves cannot share one, and having it costs no migration — which matters
    here, because this round adds no schema at all.
    """
    return engagement.updated_at.isoformat()


@transaction.atomic
def update_engagement(
    *,
    engagement: MatterEngagement,
    kind: str = _UNSET,
    title: str = _UNSET,
    url: Any = _UNSET,
    smaily_url: Any = _UNSET,
    alchemer_url: Any = _UNSET,
    note: Any = _UNSET,
    occurred_on: Any = _UNSET,
    occurred_on_precision: Any = _UNSET,
    feedback_deadline: Any = _UNSET,
    feedback_received: Any = _UNSET,
    response_count: Any = _UNSET,
    actor: Any = None,
    expected_revision: str | None = None,
) -> MatterEngagement:
    """Correct an engagement, and say nothing when nothing changed.

    The payload names the fields that moved and carries values only for the
    small ones. A note can run to paragraphs, and copying every version of it
    into the audit table would turn the history into a second, worse copy of
    the notes themselves (brief 26).

    **`_UNSET` is what protects `feedback_deadline` and `response_count`.**
    Every caller that existed before a column did — the register enrichment, the
    opinion mapping refresh, the importer — names the fields it is correcting
    and no others, so a correction to a title or a date cannot quietly clear a
    reply-by date or a response count somebody typed. An explicit ``None`` still
    clears it, which is how a wrong value is removed rather than only
    overwritten, and it is how `Muuda` answers an emptied box.

    ``response_count`` therefore has three readings and they are three different
    things: not named at all leaves what is stored, ``None`` says nobody counted,
    and ``0`` says nobody answered. The last two are distinct facts about a
    consultation and no layer here collapses them (docs/adr/0086 §2, QA-03).

    **The row is locked and re-read before anything is decided**, and the
    comparison that produces `changed` is made against *that* row rather than
    against the instance the caller arrived with. Two people correcting one
    consultation would otherwise each diff against their own stale copy, and
    the second writer would file an audit row naming fields that had already
    moved — or none, and silently discard their own correction as a no-op.
    `no_key=True` for the reason `app/matters/locks.py` gives: `SearchDocument`
    carries an `engagement` foreign key and its targeted refresh runs from
    `post_save` inside this transaction.

    **Optimistic concurrency**, exactly as `edit_entry` has it. A caller that
    knows which version its form was filled from says so in
    ``expected_revision``, and a save whose token is not the stored one raises
    :class:`EngagementEditConflict` and **writes nothing**. ``None`` means «no
    opinion» and is what every non-interactive caller passes — the importer,
    the register enrichment, a data fix, a test — none of which is holding an
    earlier version of anything. The token is compared *before* the no-op
    check: a stale form carrying the values somebody else already saved has
    still been overtaken, and answering it with a silent success would teach
    the person that their copy was current when it was not.

    ``engagement`` is kept consistent with what was written, so a caller that
    goes on reading the instance it passed — `app.legacy_import.register_outreach`
    compares its own fields before and after — sees the stored values.
    """
    locked = MatterEngagement.objects.select_for_update(no_key=True).get(pk=engagement.pk)
    if expected_revision is not None and engagement_revision_token(locked) != expected_revision:
        # Read *after* the lock, so the version compared against is the one that
        # is committed rather than the one that was on screen.
        raise EngagementEditConflict(locked)

    proposed: dict[str, Any] = {}
    if kind is not _UNSET:
        proposed["kind"] = _engagement_kind(kind)
    if title is not _UNSET:
        clean_title = title.strip()
        if not clean_title:
            raise DomainError("Kaasamisel peab olema pealkiri.")
        proposed["title"] = clean_title[:500]
    if url is not _UNSET:
        proposed["url"] = normalize_engagement_url(url)
    # `_UNSET`, not `""`. A caller that does not mention a provider link leaves
    # it exactly as it was: the importer names only `url`, so a mapping refresh
    # cannot silently erase a Smaily address somebody typed on the Teema page.
    if smaily_url is not _UNSET:
        proposed["smaily_url"] = normalize_engagement_url(smaily_url)
    if alchemer_url is not _UNSET:
        proposed["alchemer_url"] = normalize_engagement_url(alchemer_url)
    if note is not _UNSET:
        proposed["note"] = (note or "").strip()
    if feedback_received is not _UNSET:
        proposed["feedback_received"] = (feedback_received or "").strip()
    # The same validator `add_engagement` writes through, so a count cannot be
    # corrected into a shape it could not have been created in — and so «7.2»,
    # «-1» and «kolm» are one sentence rather than three different errors
    # depending on which door they arrived at (`_engagement_response_count`).
    if response_count is not _UNSET:
        proposed["response_count"] = _engagement_response_count(response_count)
    if occurred_on is not _UNSET:
        proposed["occurred_on"] = occurred_on
    if feedback_deadline is not _UNSET:
        proposed["feedback_deadline"] = feedback_deadline
    if occurred_on_precision is not _UNSET:
        proposed["occurred_on_precision"] = occurred_on_precision

    # The two columns are one fact and are normalised together, against the
    # date this save *results in* rather than the one it named. Clearing
    # `Kaasamise kuupäev` on a record stored as *oktoober 2026* would otherwise
    # leave `MONTH` behind on a row with no anchor — a period the record can no
    # longer render (`_engagement_precision`). A correction naming neither
    # column touches neither: the normalised value then equals what is stored
    # and drops out of `changed`.
    if "occurred_on" in proposed or "occurred_on_precision" in proposed:
        proposed["occurred_on_precision"] = _engagement_precision(
            proposed.get("occurred_on", locked.occurred_on),
            proposed.get("occurred_on_precision", locked.occurred_on_precision),
        )

    # A wait that no longer exists cannot stay completed. Clearing
    # `Tagasisidet ootame kuni` on a round whose wait somebody had already
    # finished would otherwise leave a closure timestamp with nothing behind
    # it — a row the `matters_engagement_feedback_closure_needs_deadline`
    # check refuses and `has_open_feedback_wait` could read as neither open nor
    # closed. So the two closure columns are normalised against the deadline
    # this save *results in*, in the service, where the refusal is a sentence
    # rather than an `IntegrityError` out of a composer transaction.
    #
    # The other direction is deliberately not symmetrical: **setting** a
    # deadline on a round that has none opens a new wait and leaves the closure
    # columns exactly as they are, which for every such row is `NULL`. A
    # correction cannot reopen a wait somebody closed, because the deadline it
    # was closed against is still there and this branch is not reached.
    if proposed.get("feedback_deadline", locked.feedback_deadline) is None:
        if locked.feedback_closed_at is not None:
            proposed["feedback_closed_at"] = None
            proposed["feedback_closed_by_id"] = None

    changed = [field for field, value in proposed.items() if getattr(locked, field) != value]
    if not changed:
        return locked

    payload: dict[str, Any] = {"fields": sorted(changed)}
    if "kind" in changed:
        payload["kind_from"] = locked.kind
        payload["kind_to"] = proposed["kind"]
    if "occurred_on" in changed:
        payload["occurred_on_from"] = locked.occurred_on.isoformat() if locked.occurred_on else None
        payload["occurred_on_to"] = (
            proposed["occurred_on"].isoformat() if proposed["occurred_on"] else None
        )
    if "occurred_on_precision" in changed:
        # Recorded whenever it moves, including when the date itself did not:
        # *oktoober 2026* corrected to *IV kvartal 2026* keeps `2026-10-01` and
        # changes what that number means, so an audit row carrying only the
        # anchor would say nothing had happened.
        payload["occurred_on_precision_from"] = locked.occurred_on_precision
        payload["occurred_on_precision_to"] = proposed["occurred_on_precision"]
    if "feedback_received" in changed:
        # Whether the field is now filled, never its contents. Feedback runs to
        # paragraphs and lives on the record where it can be corrected.
        payload["feedback_received_filled"] = bool(proposed["feedback_received"])
    if "feedback_closed_at" in changed:
        # Only ever a clearance: this function never *sets* the timestamp —
        # `complete_engagement_feedback` does — so reaching here means a
        # correction removed the deadline the wait hung off, and the history has
        # to say that the completed wait stopped existing.
        payload["feedback_wait_reopened"] = True
    # **No `response_count_from`/`_to`, deliberately.** `ENGAGEMENT_ADDED` files
    # this column as `has_response_count` — «whether it was counted, not what the
    # count was» — on brief 26's reasoning that the number belongs on the record,
    # where a reader can correct it. That reasoning was a promise this product
    # could not keep until `Muuda` grew the box (QA-03); keeping the number out
    # of the correction payload now is what makes the two halves of one column's
    # history follow one convention instead of the correction row disclosing what
    # the creation row withheld. `fields` names it, which is what says a person
    # changed it and when.
    if "feedback_deadline" in changed:
        payload["feedback_deadline_from"] = (
            locked.feedback_deadline.isoformat() if locked.feedback_deadline else None
        )
        payload["feedback_deadline_to"] = (
            proposed["feedback_deadline"].isoformat() if proposed["feedback_deadline"] else None
        )

    for field in changed:
        setattr(locked, field, proposed[field])
        # The caller's own instance, kept in step with the row. `register_outreach`
        # reads its fields back after this returns to decide whether the refresh
        # changed anything, and an instance left holding pre-write values would
        # report every corrected row as untouched.
        setattr(engagement, field, proposed[field])
    locked.save(update_fields=[*changed, "updated_at"])
    record_change_event(
        event_type=ChangeEventType.ENGAGEMENT_CHANGED,
        matter=locked.matter,
        actor=actor,
        obj=locked,
        summary=locked.title[:200],
        payload=payload,
    )
    return locked


@transaction.atomic
def correct_engagement(
    *,
    engagement: MatterEngagement,
    kind: str = _UNSET,
    title: str = _UNSET,
    url: Any = _UNSET,
    smaily_url: Any = _UNSET,
    alchemer_url: Any = _UNSET,
    note: Any = _UNSET,
    occurred_on: Any = _UNSET,
    occurred_on_precision: Any = _UNSET,
    feedback_deadline: Any = _UNSET,
    feedback_received: Any = _UNSET,
    response_count: Any = _UNSET,
    actor: Any = None,
    expected_revision: str | None = None,
) -> MatterEngagement:
    """`Muuda` on a `Kaasamine`, by a person, on a Matter that is open.

    The correction sibling of :func:`record_engagement`, and it exists for the
    same reason: :func:`update_engagement` has a second, legitimate writer that
    must **not** be held to the open-Matter rule.
    `app.legacy_import.register_outreach` refreshes consultations on imported
    Matters and most of those are closed, so a guard in the leaf would break
    the import rather than protect anything. The rule is therefore stated where
    a *person* writes (R2-02).

    **Correcting a consultation is normal interactive business work, and a
    closed Matter refuses it.** This is deliberately *not* the rule
    `edit_entry` keeps. An entry correction rewrites the wording of a narrative
    somebody authored and touches no canonical fact; a `Kaasamine` correction
    moves the dates, the channel, the audience and the links of a structured
    record that the chronology, the register's activity date and the search
    projection all read. If that has to change on a finished file, the file is
    reopened, the work is done and it is closed again — which leaves somebody's
    name on both decisions (docs/adr/0075 §12, docs/adr/0076 §2).

    The lock is `lock_open_matter_for_business_write`, taken on the way in and
    held for the whole transaction, so the refusal cannot be raced: a closure
    committing first makes this refuse, and this committing first makes the
    engagement part of the file the closure then shuts.

    The child is re-read **through the locked Matter**, so the row about to be
    corrected provably belongs to the file whose state the lock just answered
    for. The view has already scoped it through `visible_to`; this is the same
    invariant stated where the write happens, because a page is not a boundary.
    """
    locked_matter = lock_open_matter_for_business_write(engagement.matter_id)
    try:
        current = MatterEngagement.objects.get(pk=engagement.pk, matter=locked_matter)
    except MatterEngagement.DoesNotExist:
        raise DomainError("Seda kaasamist ei ole sellel teemal.") from None

    return update_engagement(
        engagement=current,
        kind=kind,
        title=title,
        url=url,
        smaily_url=smaily_url,
        alchemer_url=alchemer_url,
        note=note,
        occurred_on=occurred_on,
        occurred_on_precision=occurred_on_precision,
        feedback_deadline=feedback_deadline,
        feedback_received=feedback_received,
        response_count=response_count,
        actor=actor,
        expected_revision=expected_revision,
    )


# ---------------------------------------------------------------------------
# Finishing a feedback wait
# ---------------------------------------------------------------------------
#
# `Lõpeta kaasamine`. A `Kaasamine` carrying `Tagasisidet ootame kuni` is an
# open wait and shows as current work; this is the one act that ends it
# (docs/adr/0086 §6).
#
# Every rule is here rather than on a form, because a form is what one browser
# was shown and a POST is what arrives.

#: What a completion aimed at a round nobody is waiting on is told.
ENGAGEMENT_FEEDBACK_NOT_AWAITED = (
    "Sellel kaasamisel ei ole tagasiside tähtaega, nii et ootamist ei ole vaja lõpetada."
)
#: What a second completion is told. Named, because the view prints it and the
#: tests assert on it, and a sentence spelled twice drifts.
ENGAGEMENT_FEEDBACK_ALREADY_CLOSED = "Selle kaasamise tagasiside ootamine on juba lõpetatud."

#: Why a wait stopped, in the audit payload. Two values and no third: a person
#: said the round was finished, or the Matter shut underneath it.
FEEDBACK_CLOSED_BY_PERSON = "completed"
FEEDBACK_CLOSED_BY_MATTER_CLOSURE = "matter_closed"


def _close_one_feedback_wait(
    engagement: MatterEngagement,
    *,
    matter: Matter,
    actor: Any,
    reason: str,
    feedback_received: Any = _UNSET,
) -> MatterEngagement:
    """Write the closure on one already-locked row, and audit it.

    The leaf both doors share. It does not lock, does not check the Matter and
    does not decide whether the wait may be closed — its callers have done all
    three, under the Matter's own row lock, and a second opinion here would be a
    second place for those rules to be written out.

    ``matter`` is the row the caller is already holding, passed rather than read
    off ``engagement.matter``: both callers have it under lock, and the lazy
    descriptor would fetch it again — once per round on a closure that ends
    three of them.

    ``feedback_received`` is `_UNSET` for the Matter-closure path, which writes
    no words of anybody's: a file being shut is not a statement about what came
    back (docs/adr/0086 §7).
    """
    engagement.feedback_closed_at = timezone.now()
    engagement.feedback_closed_by = actor
    fields = ["feedback_closed_at", "feedback_closed_by", "updated_at"]
    if feedback_received is not _UNSET:
        engagement.feedback_received = (feedback_received or "").strip()
        fields.insert(0, "feedback_received")
    engagement.save(update_fields=fields)
    record_change_event(
        event_type=ChangeEventType.ENGAGEMENT_FEEDBACK_CLOSED,
        matter=matter,
        actor=actor,
        obj=engagement,
        summary=engagement.title[:200],
        payload={
            "reason": reason,
            # The deadline the wait was closed against, so the history can say
            # whether it was finished before or after the day it asked for.
            "feedback_deadline": (
                engagement.feedback_deadline.isoformat()
                if engagement.feedback_deadline is not None
                else None
            ),
            "closed_at": engagement.feedback_closed_at.isoformat(),
            # Whether anything was written down, never what it said.
            "has_feedback_received": bool(engagement.feedback_received),
        },
    )
    return engagement


@transaction.atomic
def complete_engagement_feedback(
    *,
    engagement: MatterEngagement,
    feedback_received: Any = _UNSET,
    actor: Any = None,
    expected_revision: str | None = None,
) -> MatterEngagement:
    """`Lõpeta kaasamine` — a person says this consultation round is finished.

    The act the waiting state exists to be ended by. Until it happens the round
    is an open `WorkItem` on the responsible lawyer's desk, before its deadline
    and after it; afterwards it is chronology (docs/adr/0086 §6).

    **Nothing has to have come back.** ``feedback_received`` is optional and an
    empty one is a real answer — «keegi ei vastanud» is a result, and a
    completion that demanded prose would make the commonest disappointing
    outcome the one thing a lawyer could not record. Files are attached by the
    caller through the ordinary evidence path
    (`app.documents.services.capture_supporting_evidence`), so nothing here
    knows about uploads.

    ``_UNSET`` leaves the stored text exactly as it is, which is what a caller
    that is not editing the words means. An explicit ``""`` clears them, which
    is how a wrongly-pasted answer is removed rather than only overwritten — the
    sentinel discipline :func:`update_engagement` already keeps.

    **A closed Matter refuses it**, like every other interactive write on this
    record. Completing a round is a decision somebody makes about live work, and
    a finished file is reopened for it, which leaves a name on both decisions
    (docs/adr/0075 §12, docs/adr/0076 §2). The lock is
    `lock_open_matter_for_business_write`, taken on the way in and held for the
    whole transaction, so the refusal cannot be raced by a closure committing
    beside it.

    **Optimistic concurrency, and no partial write.** ``expected_revision`` is
    the version the form was rendered from; a save whose token is not the stored
    one raises :class:`EngagementEditConflict` and writes *nothing* — not the
    feedback text, not the timestamp. The token is read from the row **after**
    it is locked, so the version compared against is the committed one. ``None``
    means «no opinion» and is what a shell or a test passes.

    The two refusals below are the state machine, and both are asked of the
    locked row rather than of the instance the caller arrived with:

    * a round with no `Tagasisidet ootame kuni` has no wait to finish —
      :data:`ENGAGEMENT_FEEDBACK_NOT_AWAITED`;
    * a wait somebody already finished is not finished twice —
      :data:`ENGAGEMENT_FEEDBACK_ALREADY_CLOSED`. A second press writes no
      second audit row and does not move the timestamp, so «who ended this round
      and when» keeps one answer.
    """
    locked_matter = lock_open_matter_for_business_write(engagement.matter_id)
    try:
        # Re-read **through the locked Matter**, so the row about to be written
        # provably belongs to the file whose state the lock just answered for.
        # `no_key=True` for the reason `app/matters/locks.py` gives:
        # `SearchDocument` carries an `engagement` foreign key and its targeted
        # refresh runs from `post_save` inside this transaction.
        current = MatterEngagement.objects.select_for_update(no_key=True).get(
            pk=engagement.pk, matter=locked_matter
        )
    except MatterEngagement.DoesNotExist:
        raise DomainError("Seda kaasamist ei ole sellel teemal.") from None

    if expected_revision is not None and engagement_revision_token(current) != expected_revision:
        raise EngagementEditConflict(current)
    if current.feedback_deadline is None:
        raise DomainError(ENGAGEMENT_FEEDBACK_NOT_AWAITED)
    if current.feedback_closed_at is not None:
        raise DomainError(ENGAGEMENT_FEEDBACK_ALREADY_CLOSED)

    closed = _close_one_feedback_wait(
        current,
        matter=locked_matter,
        actor=actor,
        reason=FEEDBACK_CLOSED_BY_PERSON,
        feedback_received=feedback_received,
    )
    # The caller's own instance, kept in step with the row — the discipline
    # :func:`update_engagement` keeps, and for the same reason: a view that goes
    # on rendering the object it passed must not render a wait that is open.
    engagement.feedback_received = closed.feedback_received
    engagement.feedback_closed_at = closed.feedback_closed_at
    engagement.feedback_closed_by = closed.feedback_closed_by
    return closed


#: Refused when somebody opens a wait on a round that is already waiting.
ENGAGEMENT_FEEDBACK_ALREADY_AWAITED = "Sellel kaasamisel on tagasiside ootus juba olemas."
#: Refused when the explicit wait action arrives with no day on it.
ENGAGEMENT_FEEDBACK_NEEDS_A_DAY = "Vali kuupäev, milleni tagasisidet ootad."


@transaction.atomic
def open_engagement_feedback_wait(
    *,
    engagement: MatterEngagement,
    deadline: Any,
    actor: Any = None,
    expected_revision: str | None = None,
) -> MatterEngagement:
    """`Ootan tagasisidet` — the one act that starts a round waiting.

    **Opening a wait is a decision somebody makes, not a field on a form they
    were already filling in.** docs/adr/0086 §2 asked for the reply-by date on
    `+ Kaasamine` itself and docs/adr/0091 §2 emptied its default; using it on
    real files showed that neither went far enough. Recording «19.09 — kaasati
    234 tööstusettevõtet» is a completed act, and a *question* about a reply-by
    date sitting in the middle of that form is the complexity the department
    asked to have removed — an empty box is still a box that has to be read,
    understood and skipped, every time (lawyer feedback 11, docs/adr/0091 §2 as
    narrowed).

    So the column, the wait, the work item and `Lõpeta kaasamine` all stay
    exactly as docs/adr/0086 §3 and §6 built them, and what changes is *where the
    wait comes from*: a named act on the round's own chronology row, taken by
    somebody who has decided that this file is waiting on an answer. That
    statement has a name on it, which is what the whole machinery is for.

    **``deadline`` is required here**, unlike the field it replaces. This
    function exists only to start a wait; an empty day would be an act that does
    nothing, and the caller that means «no wait» simply does not call it.
    Clearing an existing one is still `update_engagement`, which is the
    correction surface and is where the historical rows are edited.

    **Two refusals, both read from the locked row.** A round already waiting is
    not started twice — that would silently move a deadline somebody else set —
    and a closed Matter refuses the act entirely, under
    `lock_open_matter_for_business_write`, because a page is not a boundary
    (R2-02).

    **Optimistic concurrency, and no partial write.** ``expected_revision`` is
    the version the form was rendered from, compared against the row *after* it
    is locked; a stale save raises :class:`EngagementEditConflict` and writes
    nothing. The contract `complete_engagement_feedback` keeps, for the same
    reason and in the same shape.

    Writes `ENGAGEMENT_CHANGED`, not an event of its own. The round is one
    record and this moves one column on it; a second event type for «the wait
    opened» would be a second history of the same fact, and the payload already
    names the column that moved.
    """
    if deadline is None:
        raise DomainError(ENGAGEMENT_FEEDBACK_NEEDS_A_DAY)

    locked_matter = lock_open_matter_for_business_write(engagement.matter_id)
    try:
        current = MatterEngagement.objects.select_for_update(no_key=True).get(
            pk=engagement.pk, matter=locked_matter
        )
    except MatterEngagement.DoesNotExist:
        raise DomainError("Seda kaasamist ei ole sellel teemal.") from None

    if expected_revision is not None and engagement_revision_token(current) != expected_revision:
        raise EngagementEditConflict(current)
    if current.feedback_deadline is not None:
        raise DomainError(ENGAGEMENT_FEEDBACK_ALREADY_AWAITED)

    # The one relationship between the two dates, and the same one the panel and
    # the correction form keep: a reply-by day *before* the round began is not a
    # late consultation, it is a slip of the keyboard. The anchor is the period's
    # first day, so an approximate round refuses only a deadline falling before
    # the whole period began (docs/adr/0078 §3, `refuse_deadline_before_engagement`).
    #
    # Stated here as well as on the form because this is the service boundary and
    # the form is what one browser was shown. The sentence is imported rather than
    # rewritten so the two cannot drift.
    from app.matters.forms import DEADLINE_BEFORE_ENGAGEMENT

    if current.occurred_on and deadline < current.occurred_on:
        raise DomainError(DEADLINE_BEFORE_ENGAGEMENT)

    current.feedback_deadline = deadline
    current.save(update_fields=["feedback_deadline", "updated_at"])
    engagement.feedback_deadline = deadline

    record_change_event(
        event_type=ChangeEventType.ENGAGEMENT_CHANGED,
        matter=locked_matter,
        actor=actor,
        obj=current,
        summary=current.title[:200],
        payload={
            "fields": ["feedback_deadline"],
            "feedback_deadline_from": None,
            "feedback_deadline_to": deadline.isoformat(),
            # Named so a reader of the history can tell this act apart from a
            # correction that happened to move the same column.
            "wait_opened": True,
        },
    )
    return current


def close_open_feedback_waits_for_closure(
    *, matter: Matter, actor: Any = None
) -> list[MatterEngagement]:
    """Every round this Matter was still waiting on, ended with the file.

    Called from inside `close_matter`, in its transaction and under its lock, so
    a closure either shuts the Matter *and* ends its waits or does neither. This
    is the rule `end_open_action_for_closure` and
    `cancel_planned_website_overviews_for_closure` already keep, arriving at the
    third thing a closed file could otherwise keep owing: an open wait draws a
    work item, every route that could finish one refuses a closed Matter, and
    the item would therefore sit on somebody's desk permanently unfinishable
    (docs/adr/0086 §7).

    **Closure is never blocked by them.** There is no precondition here and no
    refusal: the waits are ended, each with its own auditable event naming the
    Matter closure as the reason, and closing a file with three of them is the
    same gesture as closing one with none.

    **No feedback text is written.** A file being shut says nothing about what
    members answered, and inventing «nobody replied» for them would be a result
    nobody recorded. The rounds keep whatever was already written down.

    **Reopening does not reopen them**, exactly as it does not revive a
    cancelled `NextAction` or an abandoned website plan. A reopened file that is
    genuinely still waiting gets a new deadline from somebody who has decided it
    is still waiting, which is a statement with a name on it.
    """
    waiting = (
        MatterEngagement.objects.select_for_update(no_key=True)
        .filter(matter=matter, feedback_deadline__isnull=False, feedback_closed_at__isnull=True)
        .order_by("created_at", "id")
    )
    return [
        _close_one_feedback_wait(
            engagement, matter=matter, actor=actor, reason=FEEDBACK_CLOSED_BY_MATTER_CLOSURE
        )
        for engagement in waiting
    ]


# ---------------------------------------------------------------------------
# `Ülevaade / uudis`
# ---------------------------------------------------------------------------
#
# The record, its three states and the addresses it is allowed to point at.
# Every rule here is in this module rather than on a form, because a form is
# what one browser was shown and a POST is what arrives (docs/adr/0081, as
# amended by docs/adr/0085).

#: What a refused address says. Named because three surfaces print these and the
#: tests assert on them.
#:
#: Each one says `ülevaate või uudise` rather than merely «link», because a
#: Teema page now renders three kinds of address — a `Kaasamine`'s, a
#: `Väline seisukoht`'s and this one — and a refusal that does not name its own
#: record is a refusal the reader has to locate before they can act on it.
WEBSITE_OVERVIEW_URL_NOT_A_URL = "Ülevaate või uudise link peab olema täielik veebiaadress."
WEBSITE_OVERVIEW_URL_NOT_WEB_SCHEME = (
    "Ülevaate või uudise link peab algama http:// või https:// aadressiga."
)
WEBSITE_OVERVIEW_URL_HAS_CREDENTIALS = (
    "Ülevaate või uudise link ei tohi sisaldada kasutajanime ega parooli."
)
WEBSITE_OVERVIEW_URL_TOO_LONG = (
    f"Ülevaate või uudise link on liiga pikk — kuni {WEBSITE_OVERVIEW_URL_MAX_LENGTH} tähemärki."
)
WEBSITE_OVERVIEW_NEEDS_LINK = "Avaldatud ülevaade või uudis vajab linki."
WEBSITE_OVERVIEW_ALREADY_PUBLISHED = (
    "See ülevaade või uudis on juba avaldatud. Linki ja kuupäeva saab parandada."
)
WEBSITE_OVERVIEW_CANCELLED_IS_FINAL = (
    "Tühistatud ülevaadet või uudist ei saa enam avaldada ega muuta. Lisa uus ülevaade / uudis."
)
WEBSITE_OVERVIEW_PUBLISHED_IS_NOT_CANCELLABLE = (
    "Avaldatud ülevaadet või uudist ei saa tühistada — see on juba avaldatud. "
    "Paranda link või kuupäev."
)
WEBSITE_OVERVIEW_NOT_PUBLISHED = (
    "Seda ülevaadet või uudist ei ole avaldatud, seega ei ole linki ega kuupäeva parandada."
)
WEBSITE_OVERVIEW_CONFLICT = (
    "Seda ülevaadet või uudist on vahepeal mujal muudetud. "
    "Värskenda lehte ja vaata, mis seal nüüd kirjas on."
)
WEBSITE_OVERVIEW_ADDRESS_TAKEN = (
    "Selle aadressiga ülevaade või uudis on sellel teemal juba kirjas. "
    "Sama lehte ei salvestata kaks korda."
)
#: The one uniqueness rule on this table: one live published row per address
#: per Matter (`MatterWebsiteOverview.Meta`, docs/adr/0081).
WEBSITE_OVERVIEW_ADDRESS_CONSTRAINT = "matters_website_overview_one_row_per_published_link"


def _save_published_address(locked: MatterWebsiteOverview, *, update_fields: list[str]) -> None:
    """Save a row that now holds a published address, or refuse the address.

    One live published row per address on a Matter is a deliberate rule
    (docs/adr/0081). It is asked twice. First as a question, under the lock the
    caller holds, so the ordinary case — a second tab, a double submit, a
    correction onto an address another row already holds — gets a sentence
    beside what was typed. Then by the database, inside a savepoint, because a
    question asked before a write races whatever commits in between: the
    constraint is the last line, and its refusal is turned into the same
    sentence rather than a 500 out of an aborted transaction (ENG-025, the
    pattern `record_procedural_link` uses).

    A row taken off the file holds no address (docs/adr/0102): it is excluded
    here and by the constraint, so recording a removed page again works.
    """
    taken = (
        MatterWebsiteOverview.objects.filter(
            matter_id=locked.matter_id,
            status=WebsiteOverviewStatus.PUBLISHED,
            removed_at__isnull=True,
            url=locked.url,
        )
        .exclude(pk=locked.pk)
        .exists()
    )
    if taken:
        raise DomainError(WEBSITE_OVERVIEW_ADDRESS_TAKEN)
    try:
        with transaction.atomic():
            locked.save(update_fields=update_fields)
    except IntegrityError as error:
        if WEBSITE_OVERVIEW_ADDRESS_CONSTRAINT in str(error):
            raise DomainError(WEBSITE_OVERVIEW_ADDRESS_TAKEN) from error
        raise


def normalize_overview_news_url(value: str | None) -> str:
    """Trim it, allow it to be empty, and refuse anything that is not a public web address.

    The one door every writer of `MatterWebsiteOverview.url` passes through, so
    it is where the column's own width is enforced and where the safety rule
    lives.

    **No host allow-list**, and that is docs/adr/0085 §2 replacing
    docs/adr/0081 §3. The record used to be `Kodulehe ülevaade` — a page on
    koda.ee and nothing else — and the boundary was a parsed host equal to
    `koda.ee` or ending in `.koda.ee`. It is now `Ülevaade / uudis`: the same
    write-up may appear on koda.ee, in a trade paper, on a partner
    organisation's site or in a ministry's news feed, and the set of those is
    not a list anybody can maintain. A boundary that cannot be maintained is a
    boundary that gets widened by whoever needs it widened, one host at a time,
    which is worse than not having one.

    Nothing is given up by that, because the boundary was never load-bearing for
    access: this address is not fetched, not crawled and not resolved anywhere
    on the server. It is rendered as one labelled link in a new tab with
    `rel="noopener noreferrer"`, exactly as `Väline seisukoht`'s address already
    is (docs/adr/0084 §3), and the rules that actually protect the reader are
    the ones kept below.

    **What is kept.** `http` and `https` only — `javascript:` and `data:` are
    script delivery dressed as an address and `file:` and `ftp:` point somewhere
    the reader's browser cannot usefully follow. A **parsed host**, never a
    substring, so `https://user:pw@/path` — a non-empty authority with no host
    at all — is refused. Userinfo is refused outright rather than merely
    ignored: there is no published page behind `user:pw@`, and a credential on
    the file is a credential in an audit payload, on a rendered page and in
    everybody's browser history (red-team finding F-2).

    **Refused, never truncated**, for the reason `normalize_engagement_url`
    gives: a link cut off at a thousand characters is a link that no longer
    resolves, and a stored pointer that is quietly wrong is worse than a refusal
    naming the row (finding F-1).

    **Nothing here says the page is still there.** No request is made, and a
    saved address is a record of what somebody stated, not a claim that it
    resolves today — an overview published in 2019 whose site has since been
    rebuilt is still a true record of what the Chamber did.

    An empty value is returned as an empty string rather than refused. Whether
    emptiness is allowed is a question about the *state* the record is in — a
    plan legitimately has no address — and that question is answered by
    `publish_website_overview`, which is the only caller that requires one.
    """
    return _normalize_public_link(
        value,
        max_length=WEBSITE_OVERVIEW_URL_MAX_LENGTH,
        reject_credentials=True,
        not_a_url=WEBSITE_OVERVIEW_URL_NOT_A_URL,
        not_web_scheme=WEBSITE_OVERVIEW_URL_NOT_WEB_SCHEME,
        has_credentials=WEBSITE_OVERVIEW_URL_HAS_CREDENTIALS,
        too_long=WEBSITE_OVERVIEW_URL_TOO_LONG,
    )


class WebsiteOverviewConflict(DomainError):
    """The overview changed elsewhere between rendering this form and saving it.

    Carries the row as it now stands, because a conflict a person cannot see the
    other side of is a conflict they cannot resolve — deliberately the same
    shape as :class:`PersonalNoteConflict` and :class:`EntryEditConflict`.
    """

    def __init__(self, current: MatterWebsiteOverview) -> None:
        super().__init__(WEBSITE_OVERVIEW_CONFLICT)
        self.current = current


def website_overview_revision(record: MatterWebsiteOverview) -> str:
    """Which version of an overview a rendered form was filled from.

    ``updated_at``, rather than a column of its own — the token
    `personal_note_revision` and `entry_revision_token` use, for the reasons
    they give: `auto_now` sets it on every write, PostgreSQL stores it to the
    microsecond so two saves cannot share one, and having it costs no migration.

    The *status* would not do, and neither would a counter. Two corrections to
    one published address leave the status where it was, and a token that did
    not move between them would accept the second form as current when it is one
    write behind.

    Delegates to the record's own property rather than computing the string
    again: a template renders the token too, and two spellings of one value is
    one spelling away from a form that can never be saved.
    """
    return record.revision_token


def _locked_website_overview(
    overview: MatterWebsiteOverview, expected_revision: str | None
) -> MatterWebsiteOverview:
    """The row as it actually is, with the stale-form question already asked.

    `no_key=True` for the reason `app/matters/locks.py` gives for every lock in
    this module: these transactions go on to insert a `ChangeEvent`, and a plain
    `FOR UPDATE` on a row other writers reference is how the two deadlock cycles
    in this codebase were built.

    The revision is compared **after** the lock, so the version compared against
    is the one that is committed rather than the one that was on screen, and
    **before** anything is decided, so a refusal leaves nothing behind.
    """
    locked = MatterWebsiteOverview.objects.select_for_update(no_key=True).get(pk=overview.pk)
    if expected_revision is not None and website_overview_revision(locked) != expected_revision:
        raise WebsiteOverviewConflict(locked)
    return locked


@transaction.atomic
def plan_website_overview(*, matter: Matter, actor: Any = None) -> MatterWebsiteOverview:
    """Record that this Matter is owed an overview or a news item.

    No address, no date, and no title. The record's whole content is *that the
    write-up is owed*, and asking for anything else at this moment would be
    asking a question nobody can answer yet — the page does not exist. A title
    invented here would be a title nobody chose, and it would sit beside the
    real one the day the page is published (docs/adr/0081 §1).

    **No kind is asked for either**, and that is docs/adr/0085 §1: an overview
    Koda writes about itself and a news item written about the file are one
    publication activity, and the one thing that tells a reader which they are
    looking at is the address — which a plan does not have yet and a published
    row prints as a link.

    Writes no `Entry`. One action must not become two records that can disagree,
    which is `add_engagement`'s rule and is this one's for the same reason.

    Takes no closed-Matter guard itself, deliberately: the leaf is where the row
    is created, and the rule «a closed file accepts no new business content» is
    stated where a *person* writes, in `app.matters.workspace`. That is the same
    split `add_engagement` and `record_engagement` keep, and it is what leaves
    room for a later reviewed import to file the history of an archived Matter
    without the rule having to be weakened (R2-02).
    """
    overview = MatterWebsiteOverview.objects.create(
        matter=matter,
        status=WebsiteOverviewStatus.PLANNED,
        created_by=actor,
        status_changed_at=timezone.now(),
    )
    record_change_event(
        event_type=ChangeEventType.WEBSITE_OVERVIEW_PLANNED,
        matter=matter,
        actor=actor,
        obj=overview,
        payload={"status": overview.status},
    )
    return overview


def _publication_values(url: Any, published_on: Any) -> tuple[str, Any]:
    """The one thing a publication needs, and the one it may not know.

    **The address is required and the date is not**, which is docs/adr/0089 §8
    replacing docs/adr/0081 §2. A page is published because it is up somewhere a
    reader can open it; whether anybody wrote down the day it went up is a
    second fact, frequently unknown, and refusing the save over it was making
    people answer «today» to a question they had not checked. `None` is stored
    as `NULL` and read as *unknown* — never as today, never as the day the row
    was created, and never as a sentinel date.

    The address rule is unchanged and is still the one that carries the safety
    half: a value that is not a public `http(s)` address is refused here in the
    words it is refused in everywhere.
    """
    clean_url = normalize_overview_news_url(url)
    if not clean_url:
        raise DomainError(WEBSITE_OVERVIEW_NEEDS_LINK)
    return clean_url, published_on


@transaction.atomic
def publish_website_overview(
    *,
    overview: MatterWebsiteOverview,
    url: Any,
    published_on: Any,
    actor: Any = None,
    expected_revision: str | None = None,
) -> MatterWebsiteOverview:
    """`Plaanis` → `Avaldatud`: the page exists, and this is where it is.

    The one transition that gives a record an address. It requires a public
    `http(s)` link and **nothing else**, and it refuses every other starting
    state by name: an overview or news item that is already published is
    corrected rather than published again (`correct_website_overview_link`), and
    a cancelled one is terminal — the honest record of a plan that came back is
    a new plan, not a resurrected one (docs/adr/0081 §1).

    **`published_on` is optional and may be `None`**, which is stored as `NULL`
    and means *the day is unknown* (docs/adr/0089 §8). It is not defaulted here,
    not defaulted in the form and not defaulted in the browser.

    **The date is the caller's and is never invented here.** `published_at`
    below is a different fact — the moment somebody wrote the publication down —
    and deriving one from the other would put a day on the file that nobody
    chose. That is the whole of docs/adr/0078 §2 and it is why the audit payload
    carries `None` rather than a timestamp when the day is not known.

    The status is read **from the locked row**, not from the instance the caller
    arrived with: two tabs both showing the same plan, both pressing `Avalda`,
    would otherwise both find it planned and both publish it. Whichever
    transaction takes the row first wins and the other is told what the record
    now says.
    """
    locked = _locked_website_overview(overview, expected_revision)
    if locked.status == WebsiteOverviewStatus.PUBLISHED:
        raise DomainError(WEBSITE_OVERVIEW_ALREADY_PUBLISHED)
    if locked.status == WebsiteOverviewStatus.CANCELLED:
        raise DomainError(WEBSITE_OVERVIEW_CANCELLED_IS_FINAL)

    clean_url, day = _publication_values(url, published_on)
    now = timezone.now()
    locked.status = WebsiteOverviewStatus.PUBLISHED
    locked.url = clean_url
    locked.published_on = day
    locked.published_by = actor
    locked.published_at = now
    locked.status_changed_at = now
    _save_published_address(
        locked,
        update_fields=[
            "status",
            "url",
            "published_on",
            "published_by",
            "published_at",
            "status_changed_at",
            "updated_at",
        ],
    )
    record_change_event(
        event_type=ChangeEventType.WEBSITE_OVERVIEW_PUBLISHED,
        matter=locked.matter,
        actor=actor,
        obj=locked,
        payload={
            "status": locked.status,
            # The address itself, because it is the whole content of this record
            # and a history saying only «a link was recorded» could not answer
            # «which». It is a public page: `normalize_overview_news_url` has
            # already refused any address carrying credentials.
            "url": locked.url,
            # `None` where the day is not known, rather than an invented one.
            # An audit payload is the last place a guessed business date should
            # appear: it is what a later reader reconstructs the record from
            # (docs/adr/0089 §8, §11).
            "published_on": (
                locked.published_on.isoformat() if locked.published_on is not None else None
            ),
        },
    )
    # Keep the caller's instance consistent with what was written, as
    # `edit_entry` does: several callers go on reading the object they passed.
    overview.status = locked.status
    overview.url = locked.url
    overview.published_on = locked.published_on
    overview.published_at = locked.published_at
    return locked


@transaction.atomic
def correct_website_overview_link(
    *,
    overview: MatterWebsiteOverview,
    url: Any,
    published_on: Any,
    actor: Any = None,
    expected_revision: str | None = None,
) -> MatterWebsiteOverview:
    """`Avaldatud` → `Avaldatud`: the address or the day was wrong, and is now right.

    **Clearing the day is a correction like any other.** Since docs/adr/0089 §8
    a published row may carry no publication date, so somebody who realises the
    date on the file was a guess can empty the box and the row stays exactly
    what it was: a publication, at an address, whose day is unknown. It does not
    become planned, it does not become cancelled, its address does not stop
    being valid and the date does not come back on the next save.

    A correction, not a second publication. `published_at` and `published_by`
    stay exactly as they were — they record who wrote the publication down and
    when, which a typo discovered in March does not change — and the event is
    its own type so the history can say which of the two happened.

    **Allowed on a closed Matter, and that is the point.** Closure means no new
    business content: no new plan, no publication of one, no cancellation, each
    refused by `lock_open_matter_for_business_write` on its own route. It has
    never meant that a link recorded wrongly must stay wrong, and this function
    deliberately does not so much as read `is_open` — the same rule, and the same
    reasoning, as `edit_entry` (docs/adr/0075 §12, docs/adr/0081 §5).

    Refuses anything that is not already published, read from the locked row.
    That is what stops this route being a way to publish a planned overview on a
    closed Matter: the transition that *creates* a publication is the guarded
    one, and this one can only move an address that already exists.

    Says nothing when nothing changed, like `update_engagement` — but the
    revision is compared first, because a stale form that happens to carry the
    values the other writer saved has still been overtaken, and answering it with
    a silent success would teach the person that their copy was current.
    """
    locked = _locked_website_overview(overview, expected_revision)
    if locked.status != WebsiteOverviewStatus.PUBLISHED:
        raise DomainError(
            WEBSITE_OVERVIEW_CANCELLED_IS_FINAL
            if locked.status == WebsiteOverviewStatus.CANCELLED
            else WEBSITE_OVERVIEW_NOT_PUBLISHED
        )

    clean_url, day = _publication_values(url, published_on)
    if clean_url == locked.url and day == locked.published_on:
        return locked

    payload: dict[str, Any] = {"fields": []}
    if clean_url != locked.url:
        payload["fields"].append("url")
        payload["url_from"] = locked.url
        payload["url_to"] = clean_url
    if day != locked.published_on:
        payload["fields"].append("published_on")
        payload["published_on_from"] = (
            locked.published_on.isoformat() if locked.published_on else None
        )
        # `None` when the correction is somebody *clearing* a date they now know
        # they do not know — which is an ordinary correction since
        # docs/adr/0089 §8, and the one the lawyer feedback asked for by name. A
        # cleared date does not spring back to today on the next save, because
        # nothing anywhere supplies one.
        payload["published_on_to"] = day.isoformat() if day is not None else None

    locked.url = clean_url
    locked.published_on = day
    _save_published_address(locked, update_fields=["url", "published_on", "updated_at"])
    record_change_event(
        event_type=ChangeEventType.WEBSITE_OVERVIEW_LINK_CORRECTED,
        matter=locked.matter,
        actor=actor,
        obj=locked,
        payload=payload,
    )
    overview.url = locked.url
    overview.published_on = locked.published_on
    return locked


def _cancel_one_website_overview(
    overview: MatterWebsiteOverview, *, actor: Any, reason: str
) -> MatterWebsiteOverview:
    """Write the cancellation. The caller has already locked the row and checked it.

    ``reason`` is a short machine word in the audit payload — ``"manual"`` or
    ``"matter_closed"`` — and not free text somebody typed. It is there so the
    history can distinguish a lawyer dropping one plan from a closure dropping
    every plan on the file, which are two different facts that produce the same
    row (docs/adr/0081 §5).
    """
    now = timezone.now()
    overview.status = WebsiteOverviewStatus.CANCELLED
    overview.cancelled_at = now
    overview.status_changed_at = now
    overview.save(update_fields=["status", "cancelled_at", "status_changed_at", "updated_at"])
    record_change_event(
        event_type=ChangeEventType.WEBSITE_OVERVIEW_CANCELLED,
        matter=overview.matter,
        actor=actor,
        obj=overview,
        payload={"status": overview.status, "reason": reason},
    )
    return overview


@transaction.atomic
def cancel_website_overview(
    *,
    overview: MatterWebsiteOverview,
    actor: Any = None,
    expected_revision: str | None = None,
) -> MatterWebsiteOverview:
    """`Plaanis` → `Tühistatud`: the write-up is not going to happen.

    Nothing is deleted when a plan changes. A dropped intention is what the
    department decided at the time, and quietly removing the row is how a reader
    concludes nobody ever recorded anything — the rule `FactStatus` was written
    for (Stage-2G brief 5, 33).

    **`Avaldatud` → `Tühistatud` is refused**, and not for want of a state to
    move to. The page is up: a record claiming it was never published would be
    the file disagreeing with the world, and the honest correction to an
    overview or news item that was taken down is a decision this product has not
    been asked to make (docs/adr/0081 §1, unchanged by docs/adr/0085).
    Cancelling an already-cancelled one is refused for the ordinary reason — it
    would write a second cancellation event for an act that happened once.
    """
    locked = _locked_website_overview(overview, expected_revision)
    if locked.status == WebsiteOverviewStatus.PUBLISHED:
        raise DomainError(WEBSITE_OVERVIEW_PUBLISHED_IS_NOT_CANCELLABLE)
    if locked.status == WebsiteOverviewStatus.CANCELLED:
        raise DomainError(WEBSITE_OVERVIEW_CANCELLED_IS_FINAL)
    cancelled = _cancel_one_website_overview(locked, actor=actor, reason="manual")
    overview.status = cancelled.status
    overview.cancelled_at = cancelled.cancelled_at
    return cancelled


# ---------------------------------------------------------------------------
# `Menetluse link` — where the official proceeding on a Matter lives
# ---------------------------------------------------------------------------
#
# Every refusal here names *this* record rather than saying merely «link». A
# Teema page renders four kinds of address now — a `Kaasamine`'s, a `Väline
# seisukoht`'s, an `Ülevaade / uudis`'s and this one — and a sentence that does
# not say which one it means is a sentence the reader has to locate before they
# can act on it (docs/adr/0085 §5, applied once more).
PROCEDURAL_LINK_URL_NOT_A_URL = "Menetluse link peab olema täielik veebiaadress."
PROCEDURAL_LINK_URL_NOT_WEB_SCHEME = "Menetluse link peab algama http:// või https:// aadressiga."
PROCEDURAL_LINK_URL_HAS_CREDENTIALS = "Menetluse link ei tohi sisaldada kasutajanime ega parooli."
PROCEDURAL_LINK_URL_TOO_LONG = (
    f"Menetluse link on liiga pikk — kuni {PROCEDURAL_LINK_URL_MAX_LENGTH} tähemärki."
)
PROCEDURAL_LINK_NEEDS_URL = "Menetluse link vajab veebiaadressi."
PROCEDURAL_LINK_NEEDS_KIND = "Vali, millise menetluse allikaga on tegemist."
PROCEDURAL_LINK_UNKNOWN_KIND = "Tundmatu menetluse allikas."
PROCEDURAL_LINK_DUPLICATE = (
    "See aadress on juba selle teema menetluse linkide seas. "
    "Paranda olemasolevat rida, kui liik või nimetus on vale."
)
PROCEDURAL_LINK_CONFLICT = (
    "Seda menetluse linki on vahepeal mujal muudetud. "
    "Värskenda lehte ja vaata, mis seal nüüd kirjas on."
)


def normalize_procedural_link_url(value: str | None) -> str:
    """Trim it, require it, and refuse anything that is not a public web address.

    The one door every writer of `MatterProceduralLink.url` passes through, so
    it is where the column's own width is enforced and where the safety rule
    lives.

    **No host allow-list**, and this is the caller where that matters most.
    `EIS` is not `eelnoud.valitsus.ee` and nothing else: a ministry publishes
    through more than one document register, the EU side of a file is read on
    EUR-Lex one month and on a Commission consultation page the next, and a
    Riigikogu proceeding has more than one path shape. A list of known hosts
    would have to be widened by whoever needed it widened, one deploy at a time,
    in response to ordinary editorial decisions somewhere else — and the day a
    register moved domain the file would refuse to record where the proceeding
    actually is. The *kind* beside the address is the lawyer's own statement and
    is not a claim proved from the hostname; that is docs/adr/0089 §2, and it is
    the same conclusion docs/adr/0084 §3 and docs/adr/0085 §2 reached before it
    (master specification 11.2).

    **Nothing is rewritten.** No trailing slash is added or removed, no scheme
    is upgraded, no query parameter is dropped and no punycode is expanded. A
    document register's deep link is frequently a query and nothing else, and a
    canonicaliser that «tidied» it would quietly point the row at a different
    page than the one somebody opened.

    **What is kept** is the safety half, shared with the three other public-link
    columns through `_normalize_public_link` rather than copied: `http` and
    `https` only — `javascript:` and `data:` are script delivery dressed as an
    address and `file:` and `ftp:` point where the reader's browser cannot
    usefully follow — a **parsed host** rather than a substring, userinfo
    refused outright, and a value past the column's width refused rather than
    truncated, because a link cut off is a link that no longer resolves (red-team
    findings F-1 and F-2).

    Userinfo is refused here for the reason `normalize_overview_news_url`
    refuses it: this address is pasted *now*, from a page somebody has open, so
    there is no unreusable historical value to accommodate, and a credential on
    the file is a credential in an audit payload, on a rendered page and in
    everybody's browser history.

    **Empty is refused here**, unlike the overview's door, because there is no
    state of this record that legitimately has no address: a procedural link
    with nothing to open is not a reference to anything.
    """
    url = _normalize_public_link(
        value,
        max_length=PROCEDURAL_LINK_URL_MAX_LENGTH,
        reject_credentials=True,
        not_a_url=PROCEDURAL_LINK_URL_NOT_A_URL,
        not_web_scheme=PROCEDURAL_LINK_URL_NOT_WEB_SCHEME,
        has_credentials=PROCEDURAL_LINK_URL_HAS_CREDENTIALS,
        too_long=PROCEDURAL_LINK_URL_TOO_LONG,
    )
    if not url:
        raise DomainError(PROCEDURAL_LINK_NEEDS_URL)
    return url


def normalize_procedural_link_kind(value: Any) -> str:
    """One of the five, or a refusal naming the question rather than the value.

    Read through the enum rather than trusted from a POST, because `kind` is a
    plain `CharField` and a crafted value would otherwise reach a `CHECK` as a
    `django.db.utils.IntegrityError` with an aborted transaction — the failure
    shape every refusal in this module exists to avoid.
    """
    if value in (None, ""):
        raise DomainError(PROCEDURAL_LINK_NEEDS_KIND)
    kind = str(value)
    if kind not in ProceduralLinkKind.values:
        raise DomainError(PROCEDURAL_LINK_UNKNOWN_KIND)
    return kind


def normalize_procedural_link_label(value: str | None) -> str:
    """A few words, or nothing at all.

    Trimmed and bounded, never required. Demanding a name for every address is
    exactly what AGENTS.md means by making routine capture slower with optional
    metadata: a Matter with one EIS link needs no name for it, because the kind
    beside it already says what it is.

    Refused rather than truncated past the column's width, for
    `_normalize_public_link`'s reason: a name cut off mid-word is a name that
    says something its author did not write.
    """
    label = (value or "").strip()
    if len(label) > PROCEDURAL_LINK_LABEL_MAX_LENGTH:
        raise DomainError(
            f"Menetluse lingi nimetus on liiga pikk — kuni "
            f"{PROCEDURAL_LINK_LABEL_MAX_LENGTH} tähemärki."
        )
    return label


class ProceduralLinkConflict(DomainError):
    """The link changed elsewhere between rendering a correction form and saving it.

    Carries the row as it now stands, because a conflict a person cannot see the
    other side of is a conflict they cannot resolve — deliberately the same
    shape as :class:`WebsiteOverviewConflict` and :class:`PersonalNoteConflict`.
    """

    def __init__(self, current: MatterProceduralLink) -> None:
        super().__init__(PROCEDURAL_LINK_CONFLICT)
        self.current = current


def procedural_link_revision(link: MatterProceduralLink) -> str:
    """The service-side spelling of :attr:`MatterProceduralLink.revision_token`.

    Both a template and this module need the token, and a second spelling of it
    would produce a different string for the same row and refuse every save.
    """
    return link.revision_token


def _locked_procedural_link(
    link: MatterProceduralLink, expected_revision: str | None
) -> MatterProceduralLink:
    """Take the row, then decide whether the caller's copy was current.

    The lock first and the comparison second, for `_locked_website_overview`'s
    reason: two tabs holding the same version would otherwise both read it as
    current and both write.

    ``no_key=True`` on every row lock in this application — a `FOR UPDATE` here
    blocks anything that merely references the row and is how two of this
    project's deadlock cycles were built.
    """
    locked = MatterProceduralLink.objects.select_for_update(no_key=True).get(pk=link.pk)
    if expected_revision is not None and procedural_link_revision(locked) != expected_revision:
        raise ProceduralLinkConflict(locked)
    return locked


@transaction.atomic
def record_procedural_link(
    *,
    matter: Matter,
    kind: Any,
    url: Any,
    label: str = "",
    actor: Any = None,
) -> MatterProceduralLink:
    """Record where the official proceeding on this Matter lives.

    One row: the kind of source, the address, and optionally a few words naming
    which proceeding. **Nothing is fetched** — see
    :class:`~app.matters.models.MatterProceduralLink` for why that boundary is
    the record rather than a feature it is missing (docs/adr/0089 §4).

    **Idempotent on the address, and only where the answers agree.** A
    double-click, a browser retry and a stale response all produce the same POST
    twice, and the second must not leave a Matter carrying one address twice.
    So a row already holding this address on this Matter is returned unchanged
    and writes no second audit event — the save the person meant happened, and
    telling them it failed would be a lie about a record that is there.

    Where the second submission carries a *different* kind or label, it is
    refused by name instead. Silently ignoring a changed classification would
    leave somebody looking at a row that says something they have just corrected
    and were told was saved; the honest answer is to send them to the row's own
    correction control (docs/adr/0089 §6).

    The database's `matters_procedural_link_one_row_per_address` stands behind
    both branches, for a write that did not come through here.
    """
    clean_url = normalize_procedural_link_url(url)
    clean_kind = normalize_procedural_link_kind(kind)
    clean_label = normalize_procedural_link_label(label)

    def _answer_for(existing: MatterProceduralLink) -> MatterProceduralLink:
        """What to do about an address this Matter already holds.

        The same rule whichever way the row is found — by looking first, or by
        losing the race to insert it — because they are the same situation and a
        second copy of the decision is a second place for it to drift.
        """
        if existing.kind == clean_kind and existing.label == clean_label:
            return existing
        raise DomainError(PROCEDURAL_LINK_DUPLICATE)

    existing = (
        MatterProceduralLink.objects.select_for_update(no_key=True)
        .filter(matter=matter, url=clean_url)
        .first()
    )
    if existing is not None:
        return _answer_for(existing)

    try:
        # Its own savepoint, so that losing the race below leaves this
        # transaction usable. `select_for_update` above locks the rows it finds
        # and there are none to lock, so two identical POSTs arriving together
        # both reach this insert and the unique index decides between them. The
        # loser must meet the same answer as a second click that arrived a
        # moment later — not a 500, which is what an unhandled `IntegrityError`
        # would give somebody whose save had actually succeeded
        # (docs/adr/0089 §6).
        with transaction.atomic():
            link = MatterProceduralLink.objects.create(
                matter=matter,
                kind=clean_kind,
                url=clean_url,
                label=clean_label,
                created_by=actor,
            )
    except IntegrityError:
        return _answer_for(MatterProceduralLink.objects.get(matter=matter, url=clean_url))
    record_change_event(
        event_type=ChangeEventType.PROCEDURAL_LINK_RECORDED,
        matter=matter,
        actor=actor,
        obj=link,
        payload={
            "kind": link.kind,
            # The address itself, because it is the whole content of this record
            # and a history saying only «a procedural link was added» could not
            # answer «which». It is a public page and
            # `normalize_procedural_link_url` has already refused any address
            # carrying credentials.
            "url": link.url,
            "label": link.label,
        },
    )
    return link


@transaction.atomic
def correct_procedural_link(
    *,
    link: MatterProceduralLink,
    kind: Any,
    url: Any,
    label: str = "",
    actor: Any = None,
    expected_revision: str | None = None,
) -> MatterProceduralLink:
    """The kind, the name or the address was wrong, and is now right.

    The only other thing that happens to this record. There is deliberately no
    deletion, on an open Matter or a closed one: a mistaken row is corrected,
    because what the file recorded and who recorded it is part of the file — the
    rule `MatterEngagement` and `MatterExternalPosition` both keep
    (docs/adr/0084 §8).

    **Allowed on a closed Matter, and that is the point.** Closure means no new
    business content; it has never meant that an address recorded wrongly must
    stay wrong. This function deliberately does not so much as read `is_open` —
    the same rule, and the same reasoning, as `correct_website_overview_link`
    and `edit_entry` (docs/adr/0075 §12, docs/adr/0081 §5).

    Says nothing when nothing changed, like `update_engagement` — but the
    revision is compared first, because a stale form that happens to carry the
    values the other writer saved has still been overtaken, and answering it
    with a silent success would teach the person that their copy was current.

    A correction onto an address another row on this Matter already holds is
    refused by name rather than by an `IntegrityError`, for the reason
    :func:`record_procedural_link` refuses one.
    """
    locked = _locked_procedural_link(link, expected_revision)
    clean_url = normalize_procedural_link_url(url)
    clean_kind = normalize_procedural_link_kind(kind)
    clean_label = normalize_procedural_link_label(label)

    if clean_url == locked.url and clean_kind == locked.kind and clean_label == locked.label:
        return locked

    if (
        clean_url != locked.url
        and MatterProceduralLink.objects.filter(matter_id=locked.matter_id, url=clean_url)
        .exclude(pk=locked.pk)
        .exists()
    ):
        raise DomainError(PROCEDURAL_LINK_DUPLICATE)

    payload: dict[str, Any] = {"fields": []}
    if clean_kind != locked.kind:
        payload["fields"].append("kind")
        payload["kind_from"] = locked.kind
        payload["kind_to"] = clean_kind
    if clean_url != locked.url:
        payload["fields"].append("url")
        payload["url_from"] = locked.url
        payload["url_to"] = clean_url
    if clean_label != locked.label:
        payload["fields"].append("label")
        payload["label_from"] = locked.label
        payload["label_to"] = clean_label

    locked.kind = clean_kind
    locked.url = clean_url
    locked.label = clean_label
    locked.save(update_fields=["kind", "url", "label", "updated_at"])
    record_change_event(
        event_type=ChangeEventType.PROCEDURAL_LINK_CORRECTED,
        matter=locked.matter,
        actor=actor,
        obj=locked,
        payload=payload,
    )
    # Keep the caller's instance consistent with what was written, as
    # `correct_website_overview_link` does: several callers go on reading the
    # object they passed.
    link.kind = locked.kind
    link.url = locked.url
    link.label = locked.label
    return locked


def cancel_planned_website_overviews_for_closure(
    *, matter: Matter, actor: Any = None
) -> list[MatterWebsiteOverview]:
    """Every plan this Matter still owed, dropped with the file that owed it.

    Called from inside `close_matter`, in its transaction and under its lock, so
    a closure either shuts the Matter *and* cancels its plans or does neither.
    A closed file that still says «an ülevaade / uudis is owed» would be an
    instruction nobody can act on: every route that could publish or cancel one
    refuses a closed Matter, so the plan would sit there permanently owed
    (docs/adr/0081 §5).

    **Closure is never blocked by them.** There is no precondition here and no
    refusal: the plans are cancelled, each with its own auditable event naming
    the closure as the reason, and closing a Matter carrying ten of them is the
    same gesture as closing one carrying none.

    Published overviews are untouched. They are a record of pages that exist.
    """
    planned = (
        MatterWebsiteOverview.objects.select_for_update(no_key=True)
        .filter(matter=matter, status=WebsiteOverviewStatus.PLANNED)
        .order_by("created_at", "id")
    )
    return [
        _cancel_one_website_overview(overview, actor=actor, reason="matter_closed")
        for overview in planned
    ]


# ---------------------------------------------------------------------------
# `Väline seisukoht`
# ---------------------------------------------------------------------------
#
# What another organisation said about this Matter, recorded as factual
# reference material: who said it, where a colleague can read it, and — when it
# is known — when they said it. Every rule is in this module rather than on a
# form, because a form is what one browser was shown and a POST is what arrives
# (docs/adr/0084).

#: What a position carrying nothing at all is told. Named because the form, the
#: view and the tests all print or assert on it, and a sentence spelled twice
#: drifts.
#:
#: It names all three answers, in the order the panel asks them, because a
#: refusal that named only two would send somebody looking for a file they do
#: not have (docs/adr/0084 §3, amended 2026-09-16).
EXTERNAL_POSITION_NEEDS_SOURCE = (
    "Kirjuta seisukoht või lisa link või fail — vähemalt üks neist on vajalik."
)
EXTERNAL_POSITION_NEEDS_ORGANISATION = "Vali organisatsioon, kelle seisukoht see on."
#: **Retired.** Received feedback with no author at all is no longer refused.
#:
#: The sentence asked for a name for the collection of answers where there was
#: no single author — «Liikmete küsitlus» for a survey of 234 companies. It was
#: still one answer too many: a lawyer writing down what a member said on the
#: telephone has neither a catalogue row nor a collection to name, and a form
#: that will not save without one is what makes people invent one (OWNER-01,
#: docs/adr/0101).
#:
#: Kept as a name rather than deleted, because tests and release notes cite it
#: and a reader meeting the constant should find out what happened to it rather
#: than only that it is gone. Nothing raises it.
EXTERNAL_POSITION_NEEDS_AUTHOR_OR_LABEL = (
    "Vali organisatsioon või kirjuta, millisest allikast tagasiside tuli."
)
#: What a discovered position with no organisation is told, and why it may not
#: borrow the label.
#:
#: `Allikas` is a received-feedback column. «MKM arvamus» found on a ministry's
#: website has an author by definition, and a record naming one through a free
#: text box instead of the shared catalogue would be the ninth way of naming an
#: institution docs/adr/0073 exists to prevent.
EXTERNAL_POSITION_LABEL_IS_RECEIVED_ONLY = (
    "Allikas käib ainult meile saadetud tagasiside juurde. "
    "Teiste arvamuse puhul vali organisatsioon."
)
#: What a caller marking a position as a member's is told when the record is not
#: feedback somebody sent to Koda.
#:
#: «This came from a member» is an answer to *who wrote to us*. A position Koda
#: found published somewhere was not written to Koda at all, so there is no
#: question for the box to answer and a `True` arriving on one is a value that did
#: not come off a page (docs/adr/0095 §4).
EXTERNAL_POSITION_MEMBER_IS_RECEIVED_ONLY = (
    "Liikme märget saab teha ainult meile saadetud tagasiside juures."
)
#: What a caller naming a provenance nobody may choose is told.
#:
#: `LEGACY` is what history says, not an answer a person gives, so it is refused
#: here as well as being absent from both forms' vocabularies: a panel that does
#: not draw a chip is not an endpoint that refuses one (docs/adr/0091 §3.4).
EXTERNAL_POSITION_PROVENANCE_NOT_SELECTABLE = (
    "Vali, kas tagasiside saadeti meile või on see kellegi teise arvamus."
)
#: A `Seotud kaasamine` naming a round on somebody else's file. The same
#: refusal, for the same reason, as `link_document_to_record`'s cross-Matter
#: one: a relation written across two files is a disclosure, and quietly
#: dropping the half that does not fit would be the application deciding which
#: of the two the person meant.
EXTERNAL_POSITION_ENGAGEMENT_ELSEWHERE = "Seotud kaasamine peab olema sama teema oma."

#: What a stale correction is told. The sibling of `ENGAGEMENT_EDIT_CONFLICT`
#: and deliberately the same shape of sentence.
EXTERNAL_POSITION_EDIT_CONFLICT = "Välist seisukohta on vahepeal mujal muudetud."


class ExternalPositionConflict(DomainError):
    """The position changed elsewhere between rendering a form and saving it.

    Carries the row as it now stands, because a conflict a person cannot see
    the other side of is a conflict they cannot resolve — the same reasoning,
    and deliberately the same shape, as :class:`EngagementEditConflict`.
    """

    def __init__(self, current: MatterExternalPosition) -> None:
        super().__init__(EXTERNAL_POSITION_EDIT_CONFLICT)
        self.current = current


def external_position_revision(position: MatterExternalPosition) -> str:
    """Which version of a position a rendered correction form was filled from.

    ``updated_at``, for the reasons `engagement_revision_token` gives: `auto_now`
    sets it on every write, PostgreSQL stores it to the microsecond so two saves
    cannot share one, and having it costs no extra column.
    """
    return position.updated_at.isoformat()


def _external_position_precision(stated_on: Any, value: Any) -> str:
    """How exactly `Seisukoha kuupäev` is known, normalised and vouched for.

    The two rules `_engagement_precision` keeps, for the same two reasons. A
    date nobody knows has no precision, so a missing date forces `EXACT` — and
    here the database says so as well, because the column is new and could
    afford the `CHECK` the engagement's could not. And the vocabulary is
    checked in the service so that a bad value is a `DomainError` naming what
    was wrong rather than an `IntegrityError` naming a constraint from inside a
    transaction that has already captured three files.
    """
    if stated_on is None:
        return DatePrecision.EXACT.value
    precision = value or DatePrecision.EXACT.value
    if precision not in DatePrecision.values:
        raise DomainError(f"Tundmatu kuupäeva täpsus {precision!r}.")
    return precision


def _external_position_engagement(matter: Matter, engagement: Any) -> MatterEngagement | None:
    """The `Kaasamine` this position answers, proved to be on the same Matter.

    A `CHECK` constraint sees one row and cannot follow a foreign key, so the
    invariant that both ends of this relation belong to one file is stated
    here — the same division of labour, and the same refusal rather than a
    repair, as :func:`app.documents.services.link_document_to_record`.

    ``None`` passes straight through, and that is the ordinary case: most
    external positions are unsolicited, and a required relation would make the
    commonest kind of position unrecordable (docs/adr/0084 §4).
    """
    if engagement is None:
        return None
    if engagement.matter_id != matter.pk:
        raise DomainError(EXTERNAL_POSITION_ENGAGEMENT_ELSEWHERE)
    return engagement


def _external_position_source(
    url: str, *, attachments: int, documents: int = 0, summary: str = ""
) -> None:
    """Refuse a position that records nothing at all.

    **One of three is required and any one alone is enough**: the written
    position — what the organisation said, typed into `Seisukoht` — a public
    address, or a document captured through the ordinary evidence pipeline. Any
    combination is ordinary too: a ministry that publishes a page, sends the
    paper *and* summarises it in a covering mail is one position with three
    sources, not three positions.

    Text alone is the case this rule was widened for. The commonest feedback a
    department receives is two sentences in an e-mail from a member
    association, or something an official said on the telephone that is worth
    keeping against the file; neither has a file and neither has a published
    address. Refusing them did not make the record better sourced — it made
    people invent a source, which is the one failure this record exists to
    prevent (docs/adr/0084 §3, amended 2026-09-16).

    It still cannot be a database constraint: two of the three are columns on
    this row and the third is a row in `documents_documentlink`, and a `CHECK`
    sees one row and cannot count another table. So it is stated here, at the
    two doors a person's save comes through, and it is stated *before* anything
    is written so that a refusal leaves nothing behind.

    ``summary`` is the already-stripped text the caller is about to store, not
    the raw box: a `Seisukoht` of three spaces is not a written position, and
    deciding that here rather than at each call site is what stops the two
    doors from disagreeing about it.
    """
    if url or attachments or documents or summary:
        return
    raise DomainError(EXTERNAL_POSITION_NEEDS_SOURCE)


def _external_position_authorship(
    *, provenance: Any, organisation: Any, source_label: str, source_is_member: bool = False
) -> tuple[str, str]:
    """Which provenance this save states, and who it names as the author.

    Three rules, all of them about the same thing — **a position says whose it
    is** — and all of them decided here rather than at the two call sites, so
    that creating a record and correcting one cannot disagree about what a
    complete answer looks like (docs/adr/0091 §3.3).

    1. **The provenance is one a person may choose.** `RECEIVED` or `DISCOVERED`,
       never `LEGACY`: that value is what rows written before the question
       existed say, and a save that could state it would let somebody file a new
       record as unspecified rather than answering. Refused here as well as being
       absent from both forms, because a panel that draws no chip is not an
       endpoint that refuses one.
    2. **Received feedback names an organisation or a source.** A survey of 234
       industrial companies has no single author, and the two answers this rule
       used to force were an invented organisation and one arbitrary respondent
       standing for the rest. Either column satisfies it and both together are
       ordinary.
    3. **A discovered position names an organisation**, and may not name a
       source label instead. `Allikas` is a free text box, and letting it answer
       *whose position is this* would make it a ninth way of naming an
       institution beside the one shared catalogue (docs/adr/0073).

    Returns the normalised provenance and the trimmed label, because the caller
    stores both and trimming at each site is how two doors come to disagree about
    whether three spaces are a source name. The database states rules 2 and 3 as
    `CHECK`s too — all three columns are on one row, so it can — and this is
    what turns them into an Estonian sentence under the right control rather than
    an `IntegrityError` from inside a transaction that has already read a file.
    """
    value = getattr(provenance, "value", provenance) or ""
    if value not in SELECTABLE_EXTERNAL_POSITION_PROVENANCE:
        raise DomainError(EXTERNAL_POSITION_PROVENANCE_NOT_SELECTABLE)
    label = (source_label or "").strip()[:EXTERNAL_POSITION_SOURCE_LABEL_MAX_LENGTH]
    # 4. The member marker is a received-feedback answer, exactly as `Allikas`
    #    is. Refused before the provenance branches below, so that one sentence
    #    covers `DISCOVERED` and anything else a future caller passes rather than
    #    only the branch somebody remembered to guard (docs/adr/0095 §4).
    if source_is_member and value != ExternalPositionProvenance.RECEIVED.value:
        raise DomainError(EXTERNAL_POSITION_MEMBER_IS_RECEIVED_ONLY)
    if value == ExternalPositionProvenance.DISCOVERED.value:
        if organisation is None:
            raise DomainError(EXTERNAL_POSITION_NEEDS_ORGANISATION)
        if label:
            raise DomainError(EXTERNAL_POSITION_LABEL_IS_RECEIVED_ONLY)
        return value, ""
    # 5. Received feedback may name nobody.
    #
    #    It used to require an organisation or an `Allikas`, and the refusal was
    #    `EXTERNAL_POSITION_NEEDS_AUTHOR_OR_LABEL`. The rule was meant to stop a
    #    file carrying an anonymous claim, and for `DISCOVERED` it still does
    #    (above). For feedback somebody sent *to this office* it did the
    #    opposite of what it intended: a lawyer writing down what a member said
    #    on the telephone has neither a catalogue row nor a name for a
    #    collection, and a form that will not save until one of them exists is
    #    what makes people invent one (OWNER-01, docs/adr/0101).
    #
    #    The record is still never empty — `EXTERNAL_POSITION_NEEDS_SOURCE`
    #    means a position, a link or a file is always present. What may be
    #    absent is whose it was, and that absence is itself the honest record.
    return value, label


def record_external_position(
    *,
    matter: Matter,
    organisation: Any,
    provenance: Any = ExternalPositionProvenance.DISCOVERED.value,
    source_label: str = "",
    url: str = "",
    stated_on: Any = None,
    stated_on_precision: str = DatePrecision.EXACT.value,
    summary: str = "",
    lawyer_note: str = "",
    source_is_member: bool = False,
    engagement: Any = None,
    attachment_count: int = 0,
    actor: Any = None,
) -> MatterExternalPosition:
    """Record one other organisation's stated position on this Matter.

    Writes no `Entry`. One act must not become two records — a structured
    position and a narrative note saying the same thing — because the day they
    disagree there is no way to tell which was meant (the rule `add_engagement`
    states for the same reason).

    Writes no `Submission`, no `NextAction`, no `MatterImportantDate` and no
    work item, and moves no `Matter.response_deadline`. What somebody else
    published is a fact about the world; what is owed by this office is work,
    and only the second is modelled as work (docs/adr/0084 §5).

    ``provenance`` is the distinction the lawyers asked for — whether somebody
    gave this to Koda or Koda found it somewhere — and it is **required in
    substance**: the parameter defaults to `DISCOVERED` for the historical callers
    that have no better answer, and `LEGACY` is refused, so no person's save can
    file a record as unspecified. With `RECEIVED`, ``source_label`` may answer
    *whose feedback this is* in place of an organisation, which is the one thing
    an aggregate survey result needs and the only place the authorship rule bends
    (`_external_position_authorship`, docs/adr/0091 §3).

    ``lawyer_note`` is this office's own reading of the position, and it is **not
    a source**. Nothing in :func:`_external_position_source` counts it: a record
    whose only content is Koda's comment on something nobody can read is a record
    of nothing. It is stored, rendered and audited separately from ``summary`` at
    every step, because a file that attributes this office's criticism to the
    body being criticised is a file that lies about a professional record
    (docs/adr/0091 §4).

    ``attachment_count`` is how many files the caller is about to capture
    against this record. It is a count rather than the documents themselves
    because the `DocumentLink` cannot exist until this row does, so the source
    rule has to be decided from what the caller *holds* — and deciding it here,
    before the insert, is what makes a save that records nothing leave nothing
    behind. It is one of three answers: ``summary`` and ``url`` are the other
    two, and a position carrying only the first is an ordinary complete record.
    The caller then captures the files in the same transaction, so «promised a
    file and captured none» unwinds the position with it
    (`app.matters.workspace.add_matter_external_position`).

    ``stated_on_precision`` says how exactly ``stated_on`` is known, and
    ``stated_on`` is then the **anchor** of that period — normalised through
    the same `app.workflow.dates.bounds_for` as every other period on this
    product, so a quarter stated here is the same stored value as a quarter
    stated anywhere else. An unknown date is normalised back to `EXACT`:
    absence has no precision (docs/adr/0079 §2).

    **Takes no closed-Matter lock of its own.** The person's door is
    `app.matters.workspace.add_matter_external_position`, which locks the Matter
    and refuses a closed one before it calls this — the shape `add_engagement`
    and `add_matter_engagement` already have, and the reason is the same one
    R2-02 states: a page is not a boundary.
    """
    kind, clean_label = _external_position_authorship(
        provenance=provenance,
        organisation=organisation,
        source_label=source_label,
        source_is_member=bool(source_is_member),
    )
    clean_url = normalize_external_position_url(url)
    # Trimmed *before* the source rule reads it, so a `Seisukoht` of three
    # spaces cannot be the thing that makes an otherwise empty record savable.
    clean_summary = (summary or "").strip()[:EXTERNAL_POSITION_SUMMARY_MAX_LENGTH]
    # **Not passed to the source rule, and that is the point.** A `Juristi
    # märkus` is this office's reading of a position, so a record whose only
    # content is Koda's opinion of something nobody can read is a record of
    # nothing. The three sources stay the three sources (docs/adr/0091 §4).
    clean_note = (lawyer_note or "").strip()[:EXTERNAL_POSITION_LAWYER_NOTE_MAX_LENGTH]
    _external_position_source(clean_url, attachments=attachment_count, summary=clean_summary)
    related = _external_position_engagement(matter, engagement)
    precision = _external_position_precision(stated_on, stated_on_precision)

    position = MatterExternalPosition.objects.create(
        matter=matter,
        organisation=organisation,
        provenance=kind,
        source_label=clean_label,
        url=clean_url,
        stated_on=stated_on,
        stated_on_precision=precision,
        summary=clean_summary,
        lawyer_note=clean_note,
        source_is_member=bool(source_is_member),
        engagement=related,
        created_by=actor,
    )
    record_change_event(
        event_type=ChangeEventType.EXTERNAL_POSITION_RECORDED,
        matter=matter,
        actor=actor,
        obj=position,
        summary=position.author_label[:200],
        payload={
            "provenance": position.provenance,
            "organisation": str(organisation.pk) if organisation is not None else None,
            # Whether the answers were named by a label, not what the label says:
            # the label is on the record where a reader can correct it, and an
            # audit row holding a second copy is a worse copy nobody maintains
            # (the rule `ENGAGEMENT_FEEDBACK_CLOSED` keeps for its prose).
            "has_source_label": bool(position.source_label),
            # The date and its precision together, never the anchor on its own:
            # a payload carrying `2026-10-01` and nothing else says «1 October»
            # to whoever reads it back, which is the invention docs/adr/0079 §2
            # exists to refuse.
            "stated_on": position.stated_on.isoformat() if position.stated_on else None,
            "stated_on_precision": position.stated_on_precision,
            # Whether there is an address, not what it is. The address is on the
            # record where a reader can open and correct it; the source events
            # below are what say where it points and when that moved.
            "has_url": bool(position.url),
            "has_summary": bool(position.summary),
            # **That the lawyer wrote a note, never the note.** An audit payload
            # carrying this office's comment beside the organisation's identifier
            # is the one place the two could be read back as one statement, which
            # is exactly what the column exists to prevent (docs/adr/0091 §4).
            "has_lawyer_note": bool(position.lawyer_note),
            # The member marker itself, because unlike the two above it *is* the
            # whole fact rather than a stand-in for a body of text — there is
            # nothing substantive to withhold, and «who recorded this as a
            # member's» is exactly the question an audit trail is asked
            # (docs/adr/0095 §4).
            "source_is_member": position.source_is_member,
            "engagement": str(related.pk) if related is not None else None,
        },
    )
    if position.url:
        # The source, on its own event, from the first moment it exists. A
        # history whose only «where does this point» rows were corrections
        # could not answer the question for a record nobody ever corrected.
        record_change_event(
            event_type=ChangeEventType.EXTERNAL_POSITION_SOURCE_CHANGED,
            matter=matter,
            actor=actor,
            obj=position,
            summary=position.link_label[:200],
            payload={"url_from": None, "url_to": position.url},
        )
    return position


def record_external_position_document(
    *, position: MatterExternalPosition, document: Any, actor: Any = None
) -> None:
    """Record that one captured file is the evidence for this position.

    `DOCUMENT_CREATED` and `EVIDENCE_VERSION_ADDED` already say that bytes
    arrived on the Matter; neither of them says *what they are the evidence
    for*, which is the fact the `DocumentLink` row carries and the reason this
    event exists (docs/adr/0084 §7).

    The filename is the summary because it is what a reader recognises the file
    by and it is already in the two events above; the payload carries
    identifiers, as everything here does.
    """
    record_change_event(
        event_type=ChangeEventType.EXTERNAL_POSITION_DOCUMENT_LINKED,
        matter=position.matter,
        actor=actor,
        obj=position,
        summary=document.title[:200],
        payload={
            "document": str(document.pk),
            "organisation": (
                str(position.organisation_id) if position.organisation_id is not None else None
            ),
            "provenance": position.provenance,
        },
    )


@transaction.atomic
def correct_external_position(
    *,
    position: MatterExternalPosition,
    organisation: Any,
    url: Any,
    stated_on: Any,
    stated_on_precision: Any,
    summary: Any,
    engagement: Any,
    provenance: Any = None,
    source_label: str = "",
    source_is_member: Any = None,
    lawyer_note: Any = "",
    actor: Any = None,
    expected_revision: str | None = None,
) -> MatterExternalPosition:
    """`Muuda` on a `Väline seisukoht`, by a person, on a Matter that is open.

    **Correcting a position is normal interactive business work, and a closed
    Matter refuses it.** This is deliberately the rule `correct_engagement`
    keeps and deliberately *not* the one `edit_entry` keeps: an entry
    correction rewrites the wording of a narrative somebody authored and touches
    no canonical fact, while this moves the organisation, the date, the source
    and the relation of a structured record that the chronology reads. If that
    has to change on a finished file, the file is reopened, the work is done and
    it is closed again — which leaves somebody's name on both decisions
    (docs/adr/0075 §12, docs/adr/0076 §2, docs/adr/0084 §8).

    It is deliberately *not* `correct_website_overview_link`'s narrow exception
    either. That one may run on a closed Matter because the transition it
    performs cannot create anything: it moves an address on a row that is
    already published and refuses every other state. Here every field is
    substantive and there is no such guarded half to carve out.

    **Optimistic concurrency, and a stale save writes nothing at all.** The row
    is locked, the token is compared against the *locked* row — so the version
    compared against is the committed one — and the comparison happens before
    any value is decided, so a refusal cannot have half-applied the metadata and
    left the source behind. ``None`` means «no opinion» and is what a caller
    holding no earlier version passes (`update_engagement`, QA-09).

    Every field is named on every save, unlike `update_engagement`'s `_UNSET`
    sentinel: this function has exactly one caller, a correction form that
    renders every box, so «not mentioned» is not a state it can be in — and an
    emptied box has to be able to clear a column.

    ``provenance`` is the one exception, and it is a sentinel rather than a value:
    `None` means «this form did not ask». It exists for the historical corpus —
    a `LEGACY` row whose link needs fixing must not be forced to claim a
    provenance nobody established, and `_external_position_authorship` refuses
    `LEGACY` as an answer precisely so that no *new* record can be filed as
    unspecified. A correction form that renders the control posts a real value and
    moves the column like any other field (docs/adr/0091 §3.4).
    """
    locked_matter = lock_open_matter_for_business_write(position.matter_id)
    try:
        current = MatterExternalPosition.objects.select_for_update(no_key=True).get(
            pk=position.pk, matter=locked_matter
        )
    except MatterExternalPosition.DoesNotExist:
        raise DomainError("Seda välist seisukohta ei ole sellel teemal.") from None

    if expected_revision is not None and external_position_revision(current) != expected_revision:
        raise ExternalPositionConflict(current)

    # **The provenance a correction does not mention is the one the record has.**
    # `None` means «not asked», which is what the historical corpus needs: a
    # `LEGACY` row corrected for a typo in its link must not be forced to claim a
    # provenance nobody established, and `_external_position_authorship` refuses
    # `LEGACY` as an *answer*. A form that renders the control posts a real value
    # and moves the column like any other field (docs/adr/0091 §3.4).
    if provenance is None and current.provenance == ExternalPositionProvenance.LEGACY:
        # A historical row keeps its unspecified provenance, and the two rules
        # `LEGACY` can still break are asked anyway: it must name an organisation
        # — every one of them does, because the column was `NOT NULL` when they
        # were written — and it may not acquire a received-feedback `Allikas`.
        kind = ExternalPositionProvenance.LEGACY.value
        clean_label = ""
        if organisation is None:
            raise DomainError(EXTERNAL_POSITION_NEEDS_ORGANISATION)
        if (source_label or "").strip():
            raise DomainError(EXTERNAL_POSITION_LABEL_IS_RECEIVED_ONLY)
    else:
        kind, clean_label = _external_position_authorship(
            provenance=provenance if provenance is not None else current.provenance,
            organisation=organisation,
            source_label=source_label,
            # **The mark this correction is not changing, checked against the
            # provenance it might.** `source_is_member` is absent from
            # `proposed` below, so a correction never moves it — but a caller
            # that moves the *provenance* off `RECEIVED` on a row carrying one
            # would leave the pair invalid, and the `CHECK` would answer with an
            # `IntegrityError` from inside a transaction that has already taken
            # two row locks. Asked here instead, it is an Estonian sentence on
            # the way in, which is the same reason the authorship rules are
            # stated in this helper as well as in the database
            # (docs/adr/0095 §4).
            #
            # The mark as this save would leave it: the corrected value where
            # the caller stated one, and the stored one where it did not.
            # `Muuda` states one now — the box was saveable and never
            # correctable, so a tick made by mistake was permanent and a tick
            # made on purpose was invisible (QA-014) — and the import paths
            # still pass nothing.
            source_is_member=(
                current.source_is_member if source_is_member is None else bool(source_is_member)
            ),
        )
    clean_url = normalize_external_position_url(url)
    clean_summary = (summary or "").strip()[:EXTERNAL_POSITION_SUMMARY_MAX_LENGTH]
    clean_note = (lawyer_note or "").strip()[:EXTERNAL_POSITION_LAWYER_NOTE_MAX_LENGTH]
    # The source rule, asked again and against what this save would *result*
    # in — not against what the record holds now. A correction that empties the
    # address of a position whose `Seisukoht` says what the ministry wrote is
    # an ordinary correction; one that empties the last of the three leaves a
    # record holding nothing, which is the one state this record may never be
    # in. The document half is counted from the link table rather than assumed,
    # because the files were captured by a different operation than this one
    # and this form does not render them.
    _external_position_source(
        clean_url,
        attachments=0,
        documents=current.document_links.count(),
        summary=clean_summary,
    )
    related = _external_position_engagement(locked_matter, engagement)
    precision = _external_position_precision(stated_on, stated_on_precision)

    proposed: dict[str, Any] = {
        "organisation_id": organisation.pk if organisation is not None else None,
        "provenance": kind,
        "source_label": clean_label,
        "url": clean_url,
        "stated_on": stated_on,
        "stated_on_precision": precision,
        "summary": clean_summary,
        "lawyer_note": clean_note,
        "engagement_id": related.pk if related is not None else None,
    }
    if source_is_member is not None:
        # Absent unless the caller asked, so a path that does not render the
        # box cannot clear a mark somebody set. `_external_position_authorship`
        # above has already refused the combination this could otherwise make
        # invalid — a member's mark on anything but received feedback.
        proposed["source_is_member"] = bool(source_is_member)
    changed = [field for field, value in proposed.items() if getattr(current, field) != value]
    if not changed:
        # Nothing moved, so nothing is recorded. An audit row for a save that
        # changed no value would be a history of somebody pressing a button
        # (`update_engagement`).
        return current

    payload: dict[str, Any] = {"fields": sorted(changed)}
    if "organisation_id" in changed:
        payload["organisation_from"] = (
            str(current.organisation_id) if current.organisation_id is not None else None
        )
        payload["organisation_to"] = str(organisation.pk) if organisation is not None else None
    if "provenance" in changed:
        # Both values in full. A row that moved from «meile saadetud» to «teiste
        # arvamus» changed what the file claims about how it learned something,
        # which is a change a reader auditing provenance has to be able to see.
        payload["provenance_from"] = current.provenance
        payload["provenance_to"] = kind
    if "stated_on" in changed:
        payload["stated_on_from"] = current.stated_on.isoformat() if current.stated_on else None
        payload["stated_on_to"] = stated_on.isoformat() if stated_on else None
    if "stated_on_precision" in changed:
        # Recorded whenever it moves, including when the anchor did not:
        # *oktoober 2026* corrected to *IV kvartal 2026* keeps `2026-10-01` and
        # changes what that number means, so a payload carrying only the date
        # would say nothing had happened.
        payload["stated_on_precision_from"] = current.stated_on_precision
        payload["stated_on_precision_to"] = precision
    if "engagement_id" in changed:
        payload["engagement_from"] = (
            str(current.engagement_id) if current.engagement_id is not None else None
        )
        payload["engagement_to"] = str(related.pk) if related is not None else None

    url_from = current.url
    # `Allikas` and `Juristi märkus` are in `fields` by name and nowhere else in
    # this payload, deliberately. The first is short enough to copy and is still
    # the record's to correct; the second is this office's own words, and an
    # audit table holding them beside the organisation's identifier is the one
    # place a reader could take them for the organisation's
    # (docs/adr/0091 §4, §3.3).
    for field, value in proposed.items():
        setattr(current, field, value)
        setattr(position, field, value)
    current.save(update_fields=[*changed, "updated_at"])

    record_change_event(
        event_type=ChangeEventType.EXTERNAL_POSITION_CORRECTED,
        matter=locked_matter,
        actor=actor,
        obj=current,
        summary=current.author_label[:200],
        payload=payload,
    )
    if "url" in changed:
        # Its own event beside the correction, carrying both addresses in full.
        # Where a position points is the change a reader is most likely to be
        # auditing — an address that quietly became a different page is the one
        # way this record can lie — and a history that buried it in a list of
        # moved field names could not answer it.
        record_change_event(
            event_type=ChangeEventType.EXTERNAL_POSITION_SOURCE_CHANGED,
            matter=locked_matter,
            actor=actor,
            obj=current,
            summary=current.link_label[:200],
            payload={"url_from": url_from or None, "url_to": current.url or None},
        )
    return current


#: What a `Märge` holding nothing at all is told.
#:
#: **Not «the title is missing».** A title is optional since docs/adr/0105 §4,
#: and what this refuses is a save with no sentence, no file, no stage *and* no
#: next step — a press that would leave a dated row on the file saying nothing.
#: So the sentence names the four ways to answer rather than one of them, and it
#: is raised where the four can be seen together: the operation
#: (`app.matters.workspace.add_procedural_development`), repeated by the panel
#: beside the controls (`MatterProgressForm.clean`).
#:
#: «Kirjuta, mis juhtus» rather than «mis menetluses juhtus», because the one
#: control that writes these asks `Mis juhtus?` and is no longer only about the
#: procedure: `+ Märge` absorbed `+ Menetluse areng` on 2026-09-20, and
#: «Rääkisin Justiitsministeeriumiga» is a sentence it accepts (docs/adr/0097 §6).
DEVELOPMENT_NEEDS_SOMETHING = (
    "Kirjuta, mis juhtus, või lisa fail, uus hetkeseis või järgmine tegevus."
)
#: What somebody filing next month's committee sitting as a development is told.
#:
#: `Menetluse areng` records something that **has happened** (docs/adr/0092 §3,
#: §4). A plan belongs to `Järgmiseks` or to `+ Oluline tähtaeg`, which are the
#: product's two forward-looking facts and have their own dates, their own
#: lateness and their own place on the page.
#:
#: The sentence names the record rather than the box, deliberately. «Menetluse
#: arengu kuupäev ei saa olla tulevikus» invites somebody to clear the date and
#: file the future step undated, which is the same untruth with less of it
#: written down. One wording, used by the form and by the service, so the two
#: cannot come to mean subtly different things.
DEVELOPMENT_CANNOT_BE_FUTURE = "Märge ei saa olla tulevikus."
#: What a stale correction is told. The sibling of `EXTERNAL_POSITION_EDIT_CONFLICT`
#: and deliberately the same shape of sentence.
DEVELOPMENT_EDIT_CONFLICT = "Märget on vahepeal mujal muudetud."


class ProceduralDevelopmentConflict(DomainError):
    """The development changed elsewhere between rendering a form and saving it.

    Carries the row as it now stands, because a conflict a person cannot see the
    other side of is a conflict they cannot resolve — the same reasoning, and
    deliberately the same shape, as :class:`ExternalPositionConflict`.
    """

    def __init__(self, current: MatterProceduralDevelopment) -> None:
        super().__init__(DEVELOPMENT_EDIT_CONFLICT)
        self.current = current


def development_revision(development: MatterProceduralDevelopment) -> str:
    """Which version of a development a rendered correction form was filled from."""
    return development.updated_at.isoformat()


def _development_precision(occurred_on: Any, value: Any) -> str:
    """How exactly `Kuupäev` is known, normalised and vouched for.

    The two rules `_external_position_precision` keeps, for the same two reasons.
    A date nobody knows has no precision, so a missing date forces `EXACT` — and
    the database says so as well. The vocabulary is checked here so that a bad
    value is a `DomainError` naming what was wrong rather than an `IntegrityError`
    from inside a transaction that has already captured three files.
    """
    if occurred_on is None:
        return DatePrecision.EXACT.value
    precision = value or DatePrecision.EXACT.value
    if precision not in DatePrecision.values:
        raise DomainError(f"Tundmatu kuupäeva täpsus {precision!r}.")
    return precision


def record_procedural_development(
    *,
    matter: Matter,
    title: str,
    occurred_on: Any = None,
    occurred_on_precision: str = DatePrecision.EXACT.value,
    note: str = "",
    process_phase: str = "",
    actor: Any = None,
) -> MatterProceduralDevelopment:
    """Record one step the external procedure took.

    «Ministeerium saatis eelnõu uue versiooni», «Eelnõu jõudis Riigikokku». The
    canonical fact, written once, on the Matter it belongs to.

    **Writes nothing else.** No `Entry` — one act must not become two records
    that can disagree, which is the rule `add_engagement` and
    `record_external_position` both state. No `Submission`, no
    `MatterImportantDate`, no `NextAction` and no work item: a development is
    something that has already happened, and a record that generated work would
    make every Matter carrying one read as owing something (docs/adr/0078 §3,
    docs/adr/0084 §5). A stage change and a next step may be saved *beside* it,
    and that is one atomic operation over three canonical services rather than
    three columns on this row (`app.matters.workspace.add_procedural_development`).

    **Nothing is derived from the title.** No stage is inferred from «Eelnõu
    jõudis Riigikokku», no vocabulary is matched, and no procedural link is
    created or read. Package B's links are references — where a proceeding lives
    — and a reference is not an event (docs/adr/0091 §5.6).

    ``occurred_on`` is **optional**, and that is the whole reason this record
    exists rather than an `Entry`: a development learned about months later
    frequently has no day anybody could defend. ``occurred_on_precision`` says
    how exactly it is known and ``occurred_on`` is then the **anchor** of that
    period, normalised through the same composer as every other period on this
    product. An unknown date is normalised back to `EXACT`: absence has no
    precision (docs/adr/0079 §2).

    ``title`` is **optional too, since docs/adr/0105 §4**, and an empty one is
    stored as the empty string rather than derived from anything: not from the
    note, not from a filename, not from the stage saved beside it. The chronology
    headline falls back to the word `Märge`, in the presentation layer, on a row
    whose content is the file or the step under it
    (`app.matters.timeline.development_milestone`).

    **What may not be empty is the whole operation**, and that rule is one level
    up: this function writes one record and cannot see the file, the stage or the
    next step that may be arriving with it
    (`app.matters.workspace.add_procedural_development`).

    **Takes no closed-Matter lock of its own.** The person's door is
    `app.matters.workspace.add_procedural_development`, which locks the Matter and
    refuses a closed one before it calls this — the shape `record_external_position`
    already has, and the reason is the same one R2-02 states: a page is not a
    boundary.
    """
    # Imported here rather than at module scope: `app.matters.timeline` reads this
    # module's records, so a top-level import would close the cycle.
    from app.matters.timeline import DEVELOPMENT_HEADLINE

    clean_title = (title or "").strip()[:DEVELOPMENT_TITLE_MAX_LENGTH]
    clean_note = (note or "").strip()
    precision = _development_precision(occurred_on, occurred_on_precision)
    # **The invariant, here rather than on the form.** This is the one seam every
    # writer of a `MatterProceduralDevelopment` passes through, and it is the
    # *first* write of `add_procedural_development`'s transaction — so a refusal
    # raised here unwinds the record, its evidence, the `Hetkeseis` and the
    # `Järgmiseks` together, and a caller that never renders a form cannot move
    # a Matter to `Riigikogus` on the strength of a sitting that has not
    # happened. The form adds the same refusal beside the control the person
    # typed into, from the same helper and the same sentence.
    if period_starts_after(occurred_on, precision, day=timezone.localdate()):
        raise DomainError(DEVELOPMENT_CANNOT_BE_FUTURE)

    development = MatterProceduralDevelopment.objects.create(
        matter=matter,
        title=clean_title,
        occurred_on=occurred_on,
        occurred_on_precision=precision,
        note=clean_note,
        process_phase=_development_phase(process_phase),
        created_by=actor,
    )
    record_change_event(
        event_type=ChangeEventType.PROCEDURAL_DEVELOPMENT_RECORDED,
        matter=matter,
        actor=actor,
        obj=development,
        # The headline the row will read, so a titleless `Märge` is a named line
        # in `Kõik muudatused` rather than an empty one. The same word the
        # chronology falls back to, from the same constant.
        summary=(clean_title or DEVELOPMENT_HEADLINE)[:200],
        payload={
            # The date and its precision together, never the anchor on its own: a
            # payload carrying `2026-10-01` and nothing else says «1 October» to
            # whoever reads it back, which is the invention docs/adr/0079 §2
            # exists to refuse.
            "occurred_on": development.occurred_on.isoformat() if development.occurred_on else None,
            "occurred_on_precision": development.occurred_on_precision,
            # **That the lawyer wrote a note, never the note.** It is this
            # office's judgement of what happened, and an audit payload holding it
            # beside the event's own title is the one place the two could be read
            # back as one statement (docs/adr/0091 §4, §5).
            "has_note": bool(development.note),
            # Which phase the step was placed in, so the audit trail can answer
            # «who filed this under Kooskõlastusring, and when» without reading
            # the record's current value back.
            "process_phase": development.process_phase,
        },
    )
    return development


#: A phase key this product does not know places nothing, and is not an error.
#:
#: A crafted value on a form that never offered it, and a key a later vocabulary
#: retired, are the same thing to every reader: the record is unplaced. Refusing
#: the save would make a *presentation* association able to block a business
#: write, which is a worse answer than storing nothing (`process_phases.py`).
def _development_phase(value: Any) -> str:
    from app.matters.process_phases import PHASE_KEYS

    phase = (value or "").strip()
    return phase if phase in PHASE_KEYS else ""


#: What a stale `Muuda kulgu` save is told. The sibling of
#: `MATTER_EDIT_CONFLICT`, and the same shape of sentence for the same reason:
#: the panel posts every phase at once, so a stale copy of it is stale about
#: all of them.
TIMELINE_STEPS_CONFLICT = "Menetluse kulgu on vahepeal mujal muudetud."


class TimelineStepsConflict(DomainError):
    """The rail's stored steps changed between the panel opening and saving."""


def timeline_steps_revision_token(matter: Matter) -> str:
    """Which version of the stored rail a rendered `Muuda kulgu` panel holds.

    A digest of the rows themselves rather than a timestamp, because the
    ordinary answer here *is the absence of a row*: a phase that is shown and
    undated stores nothing, so a save that restores a default deletes rows, and
    there is no `updated_at` left behind to compare against. `MAX(updated_at)`
    would go backwards on such a save and `COUNT` would collide across
    different states; the state itself does neither.

    Two identical states therefore share a token, and that is correct rather
    than the weakness `entry_revision_token` warns about in `edit_count`: a
    panel filled from a state indistinguishable from the committed one is not
    proposing to undo anything. What it excludes is the case this guard exists
    for — somebody else moved a date or hid a phase, and a panel that never saw
    it posts the whole set back.
    """
    from app.matters.models import MatterTimelineStep

    rows = (
        MatterTimelineStep.objects.filter(matter=matter)
        .order_by("phase_key")
        .values_list("phase_key", "hidden", "occurs_on", "occurs_on_precision")
    )
    state = "\n".join(
        f"{phase_key}|{int(hidden)}|{occurs_on.isoformat() if occurs_on else ''}|{precision}"
        for phase_key, hidden, occurs_on, precision in rows
    )
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


@transaction.atomic
def set_timeline_steps(
    *,
    matter: Matter,
    steps: Any,
    actor: Any = None,
    expected_revision: str | None = None,
) -> int:
    """`Muuda kulgu` — which phases this file's rail shows, and when.

    ``steps`` is an iterable of ``(phase_key, hidden, occurs_on, precision)``,
    one per phase the panel offered. The panel offers the pattern's own phases
    and nothing else, and a key outside the vocabulary is dropped rather than
    refused: a presentation preference may never block a business write, and a
    crafted key places nothing (`app/matters/process_phases.py`).

    **The ordinary answer writes no row.** A phase that is shown and carries no
    date is the default, so its row is deleted rather than stored — the table
    holds what somebody has said and stays empty for every file nobody edits.
    That is also what makes «put it back» work: removing the row restores the
    pattern, rather than storing a second kind of default.

    **Nothing else moves.** Not `Hetkeseis`, not a `Menetluse areng`, not an
    `Oluline tähtaeg`, not the chronology, and no `NextAction`: hiding a phase
    makes no Matter late and a date here is not a deadline anybody is measured
    against (docs/adr/0078 §3).

    Returns the number of rows that actually changed, so a save that moved
    nothing records nothing.
    """
    from app.matters.models import MatterTimelineStep
    from app.matters.process_phases import PHASE_KEYS

    locked_matter = lock_open_matter_for_business_write(matter.pk)
    # Read *after* the Matter row lock, so the version compared against is the
    # one that is committed rather than the one that was on screen — the
    # discipline `guard_matter_revision` and `edit_entry` follow. An absent
    # token is not a conflict, for the reason given there.
    if expected_revision and timeline_steps_revision_token(locked_matter) != expected_revision:
        raise TimelineStepsConflict(TIMELINE_STEPS_CONFLICT)
    existing = {
        row.phase_key: row
        for row in MatterTimelineStep.objects.select_for_update(no_key=True).filter(
            matter=locked_matter
        )
    }
    changed: list[str] = []
    for phase_key, hidden, occurs_on, precision in steps:
        if phase_key not in PHASE_KEYS:
            continue
        clean_precision = _development_precision(occurs_on, precision)
        row = existing.get(phase_key)
        # The default — shown, undated — is the absence of a row.
        if not hidden and occurs_on is None:
            if row is not None:
                row.delete()
                changed.append(phase_key)
            continue
        if (
            row is not None
            and row.hidden == hidden
            and row.occurs_on == occurs_on
            and row.occurs_on_precision == clean_precision
        ):
            continue
        MatterTimelineStep.objects.update_or_create(
            matter=locked_matter,
            phase_key=phase_key,
            defaults={
                "hidden": hidden,
                "occurs_on": occurs_on,
                "occurs_on_precision": clean_precision,
                "updated_by": actor,
            },
        )
        changed.append(phase_key)

    if not changed:
        # Nothing moved, so nothing is recorded. An audit row for a save that
        # changed no value would be a history of somebody pressing a button.
        return 0
    record_change_event(
        event_type=ChangeEventType.TIMELINE_STEPS_CHANGED,
        matter=locked_matter,
        actor=actor,
        obj=locked_matter,
        summary=", ".join(sorted(changed))[:200],
        payload={"phases": sorted(changed)},
    )
    return len(changed)


def record_procedural_development_document(
    *, development: MatterProceduralDevelopment, document: Any, actor: Any = None
) -> None:
    """Record that one captured file is the evidence for this development.

    `DOCUMENT_CREATED` and `EVIDENCE_VERSION_ADDED` already say that bytes arrived
    on the Matter; neither of them says that they are the ministry's revised draft
    rather than something else that turned up the same afternoon, which is the
    fact the `DocumentLink` row carries and the reason this event exists.
    """
    record_change_event(
        event_type=ChangeEventType.PROCEDURAL_DEVELOPMENT_DOCUMENT_LINKED,
        matter=development.matter,
        actor=actor,
        obj=development,
        summary=document.title[:200],
        payload={"document": str(document.pk)},
    )


@transaction.atomic
def correct_procedural_development(
    *,
    development: MatterProceduralDevelopment,
    title: Any,
    occurred_on: Any,
    occurred_on_precision: Any,
    note: Any,
    process_phase: Any = None,
    actor: Any = None,
    expected_revision: str | None = None,
) -> MatterProceduralDevelopment:
    """`Muuda` on a recorded `Menetluse areng`, by a person, on an open Matter.

    **Correcting and removing are two different acts, and this is the first.**
    Until OWNER-04 there was no delete at all, on the reasoning that what the
    file recorded and who recorded it is part of the file. That is right about a
    record of something that *happened* and wrong about a row filed on the wrong
    Teema, which is not history: correcting it leaves a sentence nobody wrote,
    dated a day nobody chose, attributed to whoever was fixing it. Removal is
    `remove_matter_record`, it is a column rather than a `DELETE`, and it leaves
    the whole trail in `Kõik muudatused` (docs/adr/0102).

    **Refused on a closed Matter**, like a `Kaasamine` correction and unlike an
    entry's: every field on this record is substantive — what happened, when, and
    what this office made of it — and correcting any of them is normal interactive
    business work, which a finished file refuses. Reopening is the way out, and it
    leaves somebody's name on both decisions (docs/adr/0076 §2, docs/adr/0084 §8).

    **Optimistic concurrency, and a stale save writes nothing at all.** The row is
    locked, the token is compared against the *locked* row — so the version
    compared against is the committed one — and the comparison happens before any
    value is decided, so a refusal cannot have half-applied the record.

    The files a development already carries are not re-posted here and cannot be
    detached by a correction: adding evidence is a different act with a different
    audit trail (`ExternalPositionEditForm`, docs/adr/0084 §8).
    """
    locked_matter = lock_open_matter_for_business_write(development.matter_id)
    try:
        current = MatterProceduralDevelopment.objects.select_for_update(no_key=True).get(
            pk=development.pk, matter=locked_matter
        )
    except MatterProceduralDevelopment.DoesNotExist:
        raise DomainError("Seda menetluse arengut ei ole sellel teemal.") from None

    if expected_revision is not None and development_revision(current) != expected_revision:
        raise ProceduralDevelopmentConflict(current)

    # **A title may be cleared, since docs/adr/0105 §4.** A record must be
    # correctable into every shape it could have been created in, and `+ Märge`
    # creates titleless ones — a sentence somebody typed and then decided was
    # restating the file under it has to have a way out.
    clean_title = (title or "").strip()[:DEVELOPMENT_TITLE_MAX_LENGTH]
    precision = _development_precision(occurred_on, occurred_on_precision)

    proposed: dict[str, Any] = {
        "title": clean_title,
        "occurred_on": occurred_on,
        "occurred_on_precision": precision,
        "note": (note or "").strip(),
    }
    # **`None` means «this caller is not answering», and is not «clear it».**
    #
    # The phase select is removed from the form entirely on a Matter whose
    # `Õigusakt` chooses no procedure, so a correction of the *title* on such a
    # record posts no `process_phase` at all — and a missing key read as an empty
    # string would silently unplace a step somebody had placed. Only a caller that
    # was actually offered the control may change it.
    if process_phase is not None:
        proposed["process_phase"] = _development_phase(process_phase)
    changed = [field for field, value in proposed.items() if getattr(current, field) != value]
    if not changed:
        # Nothing moved, so nothing is recorded. An audit row for a save that
        # changed no value would be a history of somebody pressing a button.
        return current
    # **A correction may not move the date into the future**, the same invariant
    # `record_procedural_development` states and the same sentence.
    #
    # Guarded on the date having actually *moved*, which is the difference
    # between this seam and that one. A row filed before the rule existed is left
    # exactly as it is and is not rewritten, so refusing every correction that
    # merely *carries* its stored future date would make such a row's headline
    # permanently uncorrectable — a second, quieter way of the file being unable
    # to say what happened.
    if ("occurred_on" in changed or "occurred_on_precision" in changed) and period_starts_after(
        occurred_on, precision, day=timezone.localdate()
    ):
        raise DomainError(DEVELOPMENT_CANNOT_BE_FUTURE)

    payload: dict[str, Any] = {"fields": sorted(changed)}
    if "occurred_on" in changed:
        payload["occurred_on_from"] = (
            current.occurred_on.isoformat() if current.occurred_on else None
        )
        payload["occurred_on_to"] = occurred_on.isoformat() if occurred_on else None
    if "occurred_on_precision" in changed:
        # Recorded whenever it moves, including when the anchor did not:
        # *oktoober 2026* corrected to *IV kvartal 2026* keeps `2026-10-01` and
        # changes what that number means, so a payload carrying only the date
        # would say nothing had happened.
        payload["occurred_on_precision_from"] = current.occurred_on_precision
        payload["occurred_on_precision_to"] = precision
    if "process_phase" in changed:
        # Both sides, because «moved out of Kooskõlastusring» and «placed, having
        # been unplaced» are different corrections and the audit trail is where
        # the difference is answerable.
        payload["process_phase_from"] = current.process_phase
        payload["process_phase_to"] = proposed["process_phase"]

    for field, value in proposed.items():
        setattr(current, field, value)
        setattr(development, field, value)
    current.save(update_fields=[*changed, "updated_at"])

    record_change_event(
        event_type=ChangeEventType.PROCEDURAL_DEVELOPMENT_CORRECTED,
        matter=locked_matter,
        actor=actor,
        obj=current,
        summary=current.title[:200],
        payload=payload,
    )
    return current


@transaction.atomic
def close_matter(
    *,
    matter: Matter,
    disposition: str,
    actor: Any = None,
    reason: str = "",
    successor: Matter | None = None,
) -> Matter:
    """Stop active work on the Matter, for a stated reason.

    Closure answers "why is Koda no longer working on this", which is a
    different question from where the external process stands. An act can enter
    into force with the file still open, and a file can close while the
    procedure continues elsewhere.

    ``successor`` is the `Järglane`: the Matter this one's work continues under.
    Accepted only with ``Disposition.SUPERSEDED``, because that is the one
    closure reason that asserts a continuation — attaching a successor to
    "Algataja loobus" would record a claim nobody made. It is a real
    relationship rather than a sentence in ``reason``, so "what became of this
    file" is a question a query can answer (Teema redesign §16).
    """
    if disposition not in Disposition.values:
        raise DomainError(f"Tundmatu lõpetamise põhjus {disposition!r}.")
    if successor is not None:
        if disposition != Disposition.SUPERSEDED:
            raise DomainError("Järglase saab määrata ainult siis, kui töö jätkub teise teema all.")
        if successor.pk == matter.pk:
            raise DomainError("Teema ei saa jätkuda iseenda all.")

    # The same row set_next_action locks, in the same order. Whichever
    # transaction reaches the Matter row first wins: a closure that lands first
    # makes the other call refuse, and a next action that lands first is
    # cancelled by the closure. Neither ordering can leave a closed Matter
    # carrying an open instruction (docs/adr/0011).
    #
    # **`FOR NO KEY UPDATE`, not `FOR UPDATE`** (ENG-027). The save below fires
    # the Matter's search refresh, which takes the rebuild gate's shared side;
    # a rebuild holds that gate exclusively and, at its COMMIT, needs
    # `FOR KEY SHARE` on this row for the `SearchDocument` it re-inserted.
    # `FOR UPDATE` blocks that, the closure then waits for the gate, and
    # PostgreSQL kills one of the two. The weaker mode still conflicts with
    # itself and with `FOR UPDATE`, so two closures, a closure and a reopen, and
    # a closure and a business write still take turns (app/matters/locks.py).
    if successor is None:
        locked = Matter.objects.select_for_update(no_key=True).get(pk=matter.pk)
    else:
        # **Both Matters, in the global order** (ENG-073). A `Järglane` is a
        # live pointer at another Matter, and `delete_matter` refuses to delete
        # a Matter something points at — but it decides that under the lock of
        # the Matter being deleted. Locking only this one let a deletion of the
        # successor and this closure each pass their check and both commit,
        # leaving a closed file whose continuation is a tombstone. With the
        # successor locked too, one of them waits for the other, and the
        # successor is re-read here after any deletion that won.
        rows = lock_matters_in_order(matter.pk, successor.pk)
        locked = rows[matter.pk]
        successor = rows[successor.pk]
        if locked.deleted_at is not None:
            raise Matter.DoesNotExist(matter.pk)
        if successor.deleted_at is not None:
            raise DomainError(SUCCESSOR_DELETED_REFUSAL)
    if not locked.is_open:
        raise DomainError("Teema on juba suletud.")

    matter = locked
    matter.is_open = False
    matter.disposition = disposition
    matter.disposition_reason = reason
    matter.closed_at = timezone.now()
    matter.closed_by = actor
    matter.superseded_by = successor
    matter.save(
        update_fields=[
            "is_open",
            "disposition",
            "disposition_reason",
            "closed_at",
            "closed_by",
            "superseded_by",
            "updated_at",
        ]
    )

    # A closed Matter must not keep sitting in somebody's work list.
    end_open_action_for_closure(matter=matter, actor=actor)

    # Nor keep owing the website a summary nobody is allowed to publish any
    # more. Every planned `Ülevaade / uudis` is cancelled here, in this
    # transaction and under this lock, each with its own audit event naming the
    # closure — so the file either shuts with its plans dropped or does not shut
    # at all. Closure is never *blocked* by them: an open plan is not a
    # precondition and there is nothing here that can refuse
    # (docs/adr/0081 §5).
    cancel_planned_website_overviews_for_closure(matter=matter, actor=actor)

    # Nor keep waiting for answers nobody can record any more. Every open
    # `Kaasamine` feedback wait is ended here, in this transaction and under
    # this lock, each with its own audit event naming the closure — so the file
    # either shuts with its waits ended or does not shut at all. Closure is
    # never *blocked* by one (docs/adr/0086 §7).
    close_open_feedback_waits_for_closure(matter=matter, actor=actor)

    record_change_event(
        event_type=ChangeEventType.MATTER_CLOSED,
        matter=matter,
        actor=actor,
        obj=matter,
        summary=reason[:200],
        payload={
            "disposition": disposition,
            "successor": str(successor.pk) if successor is not None else None,
        },
    )
    return matter


#: What a closure naming a successor that has since been deleted is told.
SUCCESSOR_DELETED_REFUSAL = (
    "Järglaseks valitud teemat ei ole enam olemas. "
    "Vali järglane uuesti või sulge teema ilma selleta."
)


@transaction.atomic
def reopen_matter(*, matter: Matter, actor: Any = None, reason: str = "") -> Matter:
    """Make a closed Matter current work again.

    The row is locked and re-read before the question is answered, exactly as
    `close_matter` does on the way in. Reading ``matter.is_open`` off the
    instance the caller arrived with answers a question about a moment that
    has passed: two tabs both showing the closed banner, both pressing «Ava
    uuesti…», would both find their copy closed and both record that the
    Matter was reopened — one act, two `MATTER_REOPENED` events, and an audit
    trail that cannot say which of them happened. Whichever transaction takes
    the row first reopens; the other is told «Teema on juba avatud.» and
    writes nothing.

    `no_key=True` for the reason `app/matters/locks.py` gives: this transaction
    goes on to insert a `ChangeEvent` that references the Matter, and the mode
    conflicts with itself, so a concurrent `close_matter` — which takes the same
    row at the same strength since ENG-027 — cannot interleave with it.
    """
    locked = Matter.objects.select_for_update(no_key=True).get(pk=matter.pk)
    if locked.is_open:
        raise DomainError("Teema on juba avatud.")

    # Written through the instance the caller passed, as it always was. The
    # row is this transaction's whichever Python object the UPDATE goes
    # through, and the callers that reopen and then keep reading their own
    # instance — the register cutover reactivating an archive record among
    # them — must see the fields they just changed.
    matter.is_open = True
    matter.disposition = ""
    matter.disposition_reason = ""
    matter.closed_at = None
    matter.closed_by = None
    # The successor goes with the closure that asserted it. A reopened Matter
    # is current work again, and "this continues under 2026_14" is a statement
    # about a file that has stopped — leaving it behind would have the register
    # claiming both at once.
    matter.superseded_by = None
    matter.save(
        update_fields=[
            "is_open",
            "disposition",
            "disposition_reason",
            "closed_at",
            "closed_by",
            "superseded_by",
            "updated_at",
        ]
    )

    record_change_event(
        event_type=ChangeEventType.MATTER_REOPENED,
        matter=matter,
        actor=actor,
        obj=matter,
        summary=reason[:200],
    )
    return matter


@transaction.atomic
def mark_historical_archive_inactive(
    *, matter: Matter, actor: Any = None, provenance: dict[str, Any] | None = None
) -> Matter:
    """Record that an imported archive row is no longer current work.

    Deliberately **not** :func:`close_matter`. That operation means a person is
    closing live work now, so it rightly demands a disposition and stamps the
    current time. Neither is available here: the register carried no closure
    concept before 2025, so for a 2014 row the date activity stopped and the
    reason it stopped are simply unknown (ADR 0020).

    So exactly one field moves — ``is_open`` — and the resulting shape

        record_mode=ARCHIVE, is_open=False, disposition="", closed_at=None

    is intentional and is what the closure constraint already allows: "an
    archive row is never forced to invent a closure reason it does not have".
    It reads as *historical at cutover, exact closure fact unknown*, which is
    the honest statement. It does **not** mean the Matter closed on the day
    this ran, and nothing downstream may present it that way.

    Refuses anything that is not an open ARCHIVE record. A FULL Matter is
    current work somebody activated, and the bulk historical default is not
    entitled to demote it.
    """
    if matter.record_mode != RecordMode.ARCHIVE:
        raise DomainError("Ainult arhiivikirje saab muutuda ajalooliseks.")
    if not matter.is_open:
        raise DomainError("Teema on juba suletud.")

    matter.is_open = False
    matter.save(update_fields=["is_open", "updated_at"])

    record_change_event(
        event_type=ChangeEventType.MATTER_HISTORICAL_CUTOVER_CLOSED,
        matter=matter,
        actor=actor,
        obj=matter,
        summary="Ajalooline kirje: enam mitte jooksev töö.",
        payload={**(provenance or {}), "from_is_open": True, "to_is_open": False},
    )
    return matter


@transaction.atomic
def reactivate_historical_matter(*, matter: Matter, actor: Any = None, attestation: str) -> Matter:
    """One old Matter, attested by a person as still current, becomes current.

    The narrow exception the cutover leaves open. It is deliberately per-Matter
    and deliberately requires somebody to say why: the whole point of the
    historical default is that no rule can tell a live 2019 file from a
    finished one, so only a person can (ADR 0020).

    Two existing operations in the only order that works —
    :func:`reopen_matter` first, because :func:`promote_matter_to_full` refuses
    a closed Matter, then the promotion. `reporting_year` and the reference are
    untouched by both, so the Matter keeps reporting under the year it belongs
    to.

    Refuses a Matter carrying a **real** recorded closure. Somebody wrote a
    disposition there, and reversing a professional decision is that person's
    call through the ordinary reopen route, not a side effect of a carry-over
    convenience. This wrapper only reverses the *default* the cutover applied,
    which is recognisable precisely because it invented nothing.

    No `NextAction` is created. What happens next is a decision for whoever
    picks the file up.
    """
    if not attestation.strip():
        raise DomainError("Ajaloolise teema taasavamine vajab põhjendust.")
    if matter.is_open:
        raise DomainError("Teema on juba avatud.")
    if matter.disposition or matter.closed_at is not None:
        raise DomainError(
            "Sellel teemal on tegelik salvestatud sulgemine; kasuta tavalist taasavamist."
        )

    reopen_matter(matter=matter, actor=actor, reason=attestation)
    return promote_matter_to_full(
        matter=matter,
        actor=actor,
        reason=attestation,
        provenance={"operation": "reactivate_historical_matter"},
    )


#: Origins this operation may touch. A natively created Matter is somebody's own
#: work and the register has no authority over it, however confidently a
#: spreadsheet row appears to describe the same subject.
REGISTER_MANAGED_ORIGINS: frozenset[str] = frozenset(
    {MatterOrigin.LEGACY_IMPORT, MatterOrigin.PROMOTED_LEGACY}
)


@transaction.atomic
def retire_from_current_register(
    *, matter: Matter, actor: Any = None, provenance: dict[str, Any] | None = None
) -> Matter:
    """The final snapshot says this imported Matter is no longer current work.

    A third kind of "not current", narrower and better evidenced than the two
    that exist. :func:`close_matter` means a person is closing live work now and
    rightly demands a disposition and a timestamp. Stage 2I's
    :func:`mark_historical_archive_inactive` means a pre-cutover row the
    register said nothing about. This one means the final maintained snapshot
    carries a terminal ``HETKESEIS`` for *this* Matter, or says its work
    continues under a named other one.

    It still invents nothing. The register records no closure date, no reason
    and no closing person for any of these rows, so none is written — and that
    is why the Matter is moved to ARCHIVE rather than left FULL. The closure
    constraint permits ``is_open=False`` with an empty disposition only for an
    archive record, and it is right to: a closed FULL Matter is a professional
    decision and carries the evidence of one. The resulting shape

        record_mode=ARCHIVE, is_open=False, disposition="", closed_at=None

    reads as *the final register no longer lists this as current; the exact
    closure fact is unknown*, which is the honest statement and the same one
    Stage 2I settled on (ADR 0020, ADR 0021).

    Refuses a native Matter, and refuses one carrying a real recorded closure —
    reversing or restating a professional decision is that person's call. The
    caller classifies both as REVIEW_REQUIRED rather than catching an exception.
    """
    if matter.origin not in REGISTER_MANAGED_ORIGINS:
        raise DomainError("Registri operatsioon ei muuda kohapeal loodud teemat.")
    if matter.disposition or matter.closed_at is not None:
        raise DomainError("Teemal on tegelik salvestatud sulgemine; seda ei kirjutata üle.")

    if matter.record_mode == RecordMode.ARCHIVE and not matter.is_open:
        # Already where this operation would put it. Returning unchanged is what
        # makes a second run a no-op rather than a second audit event.
        return matter

    previous_mode = matter.record_mode
    previous_open = matter.is_open

    matter.record_mode = RecordMode.ARCHIVE
    matter.is_open = False
    matter.save(update_fields=["record_mode", "is_open", "updated_at"])

    record_change_event(
        event_type=ChangeEventType.MATTER_REGISTER_CUTOVER_RETIRED,
        matter=matter,
        actor=actor,
        obj=matter,
        summary="Lõpliku registri järgi enam mitte jooksev töö.",
        payload={
            **(provenance or {}),
            "from_record_mode": previous_mode,
            "to_record_mode": matter.record_mode,
            "from_is_open": previous_open,
            "to_is_open": False,
        },
    )
    return matter


@transaction.atomic
def refresh_matter_from_register(
    *,
    matter: Matter,
    owner: Any = _UNSET,
    stage: Any = _UNSET,
    received_date: Any = _UNSET,
    response_deadline: Any = _UNSET,
    source_organisations: Any = _UNSET,
    addressee_organisation: Any = _UNSET,
    actor: Any = None,
    provenance: dict[str, Any] | None = None,
) -> tuple[Matter, dict[str, Any]]:
    """Bring one imported Matter's fields up to the approved snapshot.

    Every argument defaults to ``_UNSET``, which means *the source could not
    settle this* and is not the same as ``None``, which means *the source says
    empty*. The caller resolves each field before calling; nothing is guessed
    here.

    Only fields the register is authoritative for, and only on a
    register-managed Matter. Returns the Matter and a map of what actually
    moved, so a run that changes nothing records nothing — which is what makes
    the operation idempotent in the audit trail as well as in the data.

    Deliberately absent: ``title``. The register's wording and the department's
    may both be right, later native editing is real work, and overwriting a
    title people navigate by would be the change nobody asked for.

    ``source_organisations`` inherits the same three-way distinction now that
    the sender side is plural, and the middle case is the one worth naming:
    ``_UNSET`` means the source could not settle the sender and the canonical
    set is left alone, ``[]`` means the source says there is no sender, and a
    one-element list means it resolved exactly one. The resolved set *replaces*
    what is stored rather than being added to it — the register stayed
    authoritative for this field when it was singular, and accumulating a stale
    reading beside a fresh one would be a new behaviour nobody asked for
    (Agent-E brief 25, 27, 61).
    """
    if matter.origin not in REGISTER_MANAGED_ORIGINS:
        raise DomainError("Registri operatsioon ei muuda kohapeal loodud teemat.")

    proposed = {
        "owner": owner,
        "stage": stage,
        "received_date": received_date,
        "response_deadline": response_deadline,
        "addressee_organisation": addressee_organisation,
    }

    changed: dict[str, Any] = {}
    for field, value in proposed.items():
        if value is _UNSET:
            continue
        current = getattr(matter, field)
        current_id = getattr(current, "pk", current)
        new_id = getattr(value, "pk", value)
        if current_id == new_id:
            continue
        setattr(matter, field, value)
        changed[field] = {"from": str(current_id or ""), "to": str(new_id or "")}

    # The sender set, written through the same event rather than through
    # `set_organisations`. Calling that here would raise a second, competing
    # organisation-change event for one refresh, and this operation already has
    # an event that says what the register moved (brief 26).
    senders: list[Any] | None = None
    if source_organisations is not _UNSET:
        senders = normalize_source_organisations(source_organisations)
        current_ids = sorted(
            str(pk) for pk in matter.source_organisations.values_list("pk", flat=True)
        )
        new_ids = sorted(str(organisation.pk) for organisation in senders)
        if current_ids == new_ids:
            senders = None
        else:
            changed["source_organisations"] = {"from": current_ids, "to": new_ids}

    if not changed:
        return matter, {}

    if senders is not None:
        matter.source_organisations.set(senders)

    scalar_fields = [field for field in changed if field != "source_organisations"]
    matter.save(update_fields=[*scalar_fields, "updated_at"])
    record_change_event(
        event_type=ChangeEventType.MATTER_SOURCE_FIELDS_REFRESHED,
        matter=matter,
        actor=actor,
        obj=matter,
        summary="Väljad uuendatud lõpliku registri hetktõmmise põhjal.",
        payload={**(provenance or {}), "fields": changed},
    )
    return matter, changed


@transaction.atomic
def promote_matter_to_full(
    *, matter: Matter, actor: Any = None, reason: str = "", provenance: dict[str, Any] | None = None
) -> Matter:
    """Activate an imported archive record as current work.

    The reviewed "promote to full Matter" operation the specification describes:
    it *enriches and activates* an existing record and changes neither its
    identity nor its provenance (19.4). The reference stays, the source
    references stay, the title stays, and the imported owner, stage and dates
    stay exactly as the register left them.

    ``origin`` becomes ``PROMOTED_LEGACY``, which is what that value has always
    been for. It stays inside ``REGISTER_YEAR_ORIGINS``, so no year statistic
    moves; what it adds is the ability to ask which records the cutover
    activated rather than inferring it from a date.

    The data-quality tier moves no further than **Tier 2**. Tier 1 means
    "verified at cutover" and the specification is explicit that the active set
    is attested by people, one lawyer's slice at a time (19.5, 19.6). A bulk
    operator command has not done that, and claiming it had would put a
    verification badge on records nobody read.

    Nothing is fabricated. No next action, no submission, no sent date, no
    outcome and no closure timestamp: a promoted Matter with no ``Järgmiseks``
    correctly shows *Järgmiseks puudub*, which is useful current data quality
    rather than a hole to fill with a guess (Stage-2F brief 18, 19).
    """
    if matter.record_mode == RecordMode.FULL:
        raise DomainError("Teema on juba täielik kirje.")
    if not matter.is_open:
        # A FULL Matter that is closed must carry a closure timestamp, and the
        # register never recorded one. Promoting this would mean either
        # inventing a date or writing a row the database refuses
        # (``matters_closure_fields_consistent``).
        raise DomainError(
            "Suletud arhiivikirjet ei aktiveerita; sulgemise kuupäeva allikas ei ole."
        )

    previous_origin = matter.origin
    previous_tier = matter.data_quality_tier

    matter.record_mode = RecordMode.FULL
    matter.origin = MatterOrigin.PROMOTED_LEGACY
    if previous_tier == DataQualityTier.TIER_3_REGISTER_ARCHIVE or not previous_tier:
        matter.data_quality_tier = DataQualityTier.TIER_2_RICH_HISTORY
    matter.save(
        update_fields=["record_mode", "origin", "data_quality_tier", "updated_at"],
    )

    record_change_event(
        event_type=ChangeEventType.MATTER_PROMOTED,
        matter=matter,
        actor=actor,
        obj=matter,
        summary=reason[:200],
        payload={
            "from_record_mode": RecordMode.ARCHIVE.value,
            "to_record_mode": RecordMode.FULL.value,
            "is_open": matter.is_open,
            "from_origin": previous_origin,
            "to_origin": matter.origin,
            "from_data_quality_tier": previous_tier,
            "to_data_quality_tier": matter.data_quality_tier,
            **(provenance or {}),
        },
    )
    return matter


# ---------------------------------------------------------------------------
# Entries and the unified composer
# ---------------------------------------------------------------------------


@transaction.atomic
def add_entry(
    *,
    matter: Matter,
    body: str,
    author: Any = None,
    kind: str = EntryKind.NOTE,
    occurred_at: Any = None,
    organisation: Any = None,
    visibility_override: str = "",
) -> Entry:
    """Record one piece of professional chronology.

    The body is sanitised here rather than in the view, so there is no caller
    that can store authored markup which has not been through the allowlist.
    """
    if kind not in EntryKind.values:
        raise DomainError(f"Tundmatu sissekande liik {kind!r}.")
    try:
        validate_visibility_override(visibility_override)
    except ValueError as error:
        raise DomainError(str(error)) from error

    clean_body = sanitize_entry_html(body)
    if is_empty(clean_body):
        raise DomainError("Sissekanne vajab sisu.")

    entry = Entry.objects.create(
        matter=matter,
        author=author,
        kind=kind,
        occurred_at=occurred_at or timezone.now(),
        body=clean_body,
        organisation=organisation,
        visibility_override=visibility_override,
    )

    record_change_event(
        event_type=ChangeEventType.ENTRY_ADDED,
        matter=matter,
        actor=author,
        obj=entry,
        summary=excerpt(clean_body, 200),
        payload={"kind": kind},
    )
    return entry


#: What a stale correction is told, in one place because a view, a template and
#: a test all have to agree about it.
ENTRY_EDIT_CONFLICT = "Sissekannet on vahepeal mujal muudetud."


class EntryEditConflict(DomainError):
    """The entry changed elsewhere between rendering this form and saving it.

    Carries the row as it now stands, because a conflict a person cannot see
    the other side of is a conflict they cannot resolve — the same reasoning,
    and deliberately the same shape, as :class:`PersonalNoteConflict`.
    """

    def __init__(self, current: Entry) -> None:
        super().__init__(ENTRY_EDIT_CONFLICT)
        self.current = current


def entry_revision_token(entry: Entry) -> str:
    """Which version of an entry a rendered edit form was filled from.

    ``updated_at``, rather than a column of its own — the token
    `personal_note_revision` uses, for the same reasons. It is set by
    `auto_now` on every write, PostgreSQL stores it to the microsecond so two
    saves cannot share one, and having it costs no migration.

    ``edit_count`` would not do. A correction reverted and re-applied would
    return it to a value a stale form is still holding, and the form would then
    be accepted as current when it is two writes behind.
    """
    return entry.updated_at.isoformat()


@transaction.atomic
def edit_entry(
    *, entry: Entry, body: str, actor: Any = None, expected_revision: str | None = None
) -> Entry:
    """Change an entry's text, keeping what it said before.

    Correcting a typo should not require a correction note, but the earlier
    wording is preserved so an edit cannot silently rewrite the record.

    **A correction, not a new fact.** ``occurred_at`` is untouched, so the line
    stays exactly where it is in the chronology; no `Entry` is created, none is
    removed, and nothing here asks whether the Matter is open. That last part is
    deliberate: a closed Matter accepts no new business work — the gate for
    that is `lock_open_matter_for_business_write`, which this function does not
    take and must not — but its history stays correctable, because the
    alternative is a record everybody knows is wrong and nobody may fix.

    **Optimistic concurrency.** The row lock below protects the revision chain;
    it does not tell a second editor that their browser was stale. A caller
    that knows which version its form was filled from says so in
    ``expected_revision``, and a save whose token is not the stored one raises
    :class:`EntryEditConflict` and **writes nothing**. ``None`` means «no
    opinion», and is for the caller with no earlier version to be holding — a
    data fix, a migration, a test.

    The token is compared *before* the no-op check rather than after. A stale
    form that happens to carry the same words the other writer saved has still
    been overtaken, and answering it with a silent success would teach the
    person that their copy was current when it was not.
    """
    clean_body = sanitize_entry_html(body)
    if is_empty(clean_body):
        raise DomainError("Sissekanne vajab sisu.")

    # Lock the row and re-read it before deciding anything. Two people editing
    # the same entry at once would otherwise both compute the same revision
    # number from a stale copy: one revision would collide, and one version of
    # the wording would be lost. The second writer waits, then edits whatever is
    # current by then.
    #
    # `no_key=True`, like every other lock in this module. `SearchDocument`
    # carries an `entry` foreign key, and its targeted refresh runs from
    # `post_save` inside the writing transaction while a full rebuild holds the
    # indexing gate and inserts `SearchDocument` rows referencing entries. A
    # plain `FOR UPDATE` here conflicts with the `FOR KEY SHARE` those inserts
    # take — the second deadlock cycle `app/matters/locks.py` describes, with
    # `Entry` where it names `Submission`. `FOR NO KEY UPDATE` still serialises
    # two editors against each other, which is the whole job of this lock.
    locked = Entry.objects.select_for_update(no_key=True).get(pk=entry.pk)
    if expected_revision is not None and entry_revision_token(locked) != expected_revision:
        # Read *after* the lock, so the version compared against is the one that
        # is committed rather than the one that was on screen.
        raise EntryEditConflict(locked)
    if clean_body == locked.body:
        return locked

    EntryRevision.objects.create(
        entry=locked,
        revision_number=locked.edit_count + 1,
        body=locked.body,
        edited_by=actor,
    )

    locked.body = clean_body
    locked.edit_count += 1
    locked.edited_at = timezone.now()
    locked.save(update_fields=["body", "edit_count", "edited_at", "updated_at"])

    record_change_event(
        event_type=ChangeEventType.ENTRY_EDITED,
        matter=locked.matter,
        actor=actor,
        obj=locked,
        payload={"revision": locked.edit_count},
    )

    # Keep the caller's instance consistent with what was written.
    entry.body = locked.body
    entry.edit_count = locked.edit_count
    entry.edited_at = locked.edited_at
    return locked


@dataclass
class ComposerResult:
    """Everything one professional update wrote, and the id that ties it.

    Returned rather than a tuple because a save can now produce six things and
    a caller unpacking positionally would silently take the wrong one the day a
    seventh is added.
    """

    operation_id: uuid.UUID
    entry: Entry | None = None
    document: Any = None
    action: Any = None
    important_date: Any = None
    engagement: MatterEngagement | None = None
    submission: Any = None
    work_victory: Any = None
    effective_date: Any = None
    closed: bool = False


@transaction.atomic
def compose_update(
    *,
    matter: Matter,
    author: Any,
    body: str = "",
    kind: str = EntryKind.NOTE,
    occurred_at: Any = None,
    organisation: Any = None,
    next_action: dict[str, Any] | None = None,
    attachment: Any = None,
    attachment_role: str = DocumentRole.OTHER,
    important_date: dict[str, Any] | None = None,
    effective_date: dict[str, Any] | None = None,
    work_victory: dict[str, Any] | None = None,
    engagement: dict[str, Any] | None = None,
    closure: dict[str, Any] | None = None,
) -> ComposerResult:
    """The unified composer: one save, one transaction, one professional update.

    This is the adoption feature. A routine update today means editing an Excel
    row and then writing the same thing into a OneNote page; here it is one box
    and one save, and everything else the same action happened to involve — a
    file, the next step, a deadline somebody announced, the consultation that
    informed it, the decision to close — rides along with it.

    **Atomicity is the substance of it, not a technicality.** If the entry
    saved and the action did not, the lawyer would believe both landed while the
    work queue quietly disagreed with the record. Everything below happens
    inside one transaction, so a refusal anywhere leaves the Matter exactly as
    it was.

    **Order matters in one place.** Closure runs last, because
    :func:`close_matter` ends the open next action and refuses a Matter that is
    already shut — so a save that both set a next step and closed would
    otherwise leave an instruction on a closed file, and one that closed before
    capturing evidence would have the evidence refused.

    **Every sub-action goes through its own service.** Nothing here writes a
    model field: the deadline is ``add_important_date``, the consultation is
    ``add_engagement``, the closure is :func:`close_matter`, the sent opinion is
    the canonical Submission workflow. Their invariants, their audit rows and
    their authorization checks are unchanged, which is the point — a unified
    surface is not a unified rule set (Teema redesign §11, §34).

    **One operation identifier ties the audit rows together**, so the human
    timeline can render one line for one action without a single canonical
    record being suppressed or merged (``app/audit/operations.py``).

    **Nothing on the Teema page posts here any more, and that is exactly why
    the closed-Matter guard matters.** docs/adr/0075 kept this route working on
    purpose, for the browsers still holding a page that posts to it. A page
    rendered before a closure is the stale tab R2-02 is about, and this is the
    route it posts to — so the rule cannot live in whether the new workspace
    renders a form. It is taken here, at the start, under the Matter lock, like
    every operation in `app/matters/workspace.py`.

    ``closure`` is unaffected: it is still the last thing this does, and closing
    an open Matter is still allowed. What is refused is a composer save arriving
    at a Matter somebody has already closed.
    """
    wants_something = bool(
        body.strip()
        or next_action
        or attachment is not None
        or important_date
        or effective_date
        or work_victory
        or engagement
        or closure
    )
    if not wants_something:
        raise DomainError("Täida sissekanne või vali, mida veel salvestada.")

    matter = lock_open_matter_for_business_write(matter.pk)

    with composer_operation() as operation_id:
        result = ComposerResult(operation_id=operation_id)

        if body.strip():
            result.entry = add_entry(
                matter=matter,
                body=body,
                author=author,
                kind=kind,
                occurred_at=occurred_at,
                organisation=organisation,
            )

        if attachment is not None:
            # An attachment is evidence like any other: same immutability, same
            # checksum, same provenance. It is captured inside this transaction,
            # so a failed save leaves neither the note nor the file behind.
            #
            # The role is chosen in the upload control before the file is
            # committed, never repaired afterwards — a document filed as "Muu"
            # because the form asked too late is a document nobody finds
            # (Teema redesign §23.5).
            upload = read_upload(attachment)
            result.document = create_document(
                matter=matter,
                title=upload.filename,
                role=attachment_role,
                created_by=author,
            )
            add_evidence_version(
                document=result.document,
                content=upload.content,
                original_filename=upload.filename,
                mime_type=upload.mime_type,
                uploaded_by=author,
            )

        if important_date:
            from app.intelligence.services import add_important_date

            result.important_date = add_important_date(
                matter=matter, actor=author, **important_date
            )

        if effective_date:
            # `+ Jõustumine`, through the canonical commencement service. Not an
            # `Entry` describing one and not a second effective-date model: the
            # composer is a new way in to `MatterEffectiveDate`, not a new place
            # to keep commencements (docs/adr/0074 §7).
            from app.intelligence.services import add_effective_date

            result.effective_date = add_effective_date(
                matter=matter, actor=author, **effective_date
            )

        if work_victory:
            # A win is recorded when it happens, which is not necessarily when
            # the file is closed. `+ Töövõit` therefore stands on its own here,
            # beside the closure's own victory rather than inside it, and both
            # go through the same confirmed-victory service (docs/adr/0074 §8).
            from app.intelligence.services import add_confirmed_work_victory

            result.work_victory = add_confirmed_work_victory(
                matter=matter, actor=author, **work_victory
            )

        if engagement:
            result.engagement = add_engagement(matter=matter, actor=author, **engagement)

        if next_action:
            # The composer is a person typing, so the *new work* boundary: a
            # step nobody named a person for goes to the Matter's owner only
            # while that owner is still somebody the department gives work to
            # (app/workflow/services.py `responsible_for_new_work`, ADR 0036).
            result.action = set_next_action_for_new_work(matter=matter, actor=author, **next_action)

        if closure:
            _apply_closure(matter=matter, author=author, result=result, closure=closure)

        return result


def _closure_final_opinion(*, matter: Matter, author: Any, final_opinion: dict[str, Any]) -> Any:
    """The sent opinion, from the file in front of the person closing the file.

    Four canonical acts and not one shortcut between them: the upload becomes a
    ``Document`` on *this* Matter under `KODA_SUBMISSION_FINAL`, that document
    gets an immutable ``DocumentVersion`` through the evidence service, the
    submission is created with its recipients, and only then is it marked sent
    with that exact version bound as its final evidence.

    Nothing about the invariant moved. ``mark_submission_sent`` still refuses a
    submission with no final version and still re-checks that the evidence is
    not less restricted than the Matter — the evidence simply arrives in the
    same transaction now instead of in a visit beforehand
    (Teema closing redesign §4, §21).

    **The title is `matter.title`.** ``Submission.title`` is mandatory in the
    canonical model and asking for it here made somebody retype the name of the
    file they had open. Deriving it is not a hidden field: no title is
    editable, submitted or defaulted anywhere in this workflow, and the
    standalone Arvamused workflow still asks for its own (§5).

    **`Saatmise kuupäev` is a day, so it is stored as one.** The anchor is
    midnight in the department's timezone and ``SentAtPrecision.DATE`` is what
    stops the UI reading that anchor back as the hour the letter went out (§6).
    """
    from app.organisations.services import resolve_recipients
    from app.submissions.enums import SentAtPrecision
    from app.submissions.services import (
        create_submission,
        mark_submission_sent,
        select_final_evidence,
    )

    upload = read_upload(final_opinion["upload"])
    document = create_document(
        matter=matter,
        title=upload.filename,
        role=DocumentRole.KODA_SUBMISSION_FINAL,
        created_by=author,
    )
    version = add_evidence_version(
        document=document,
        content=upload.content,
        original_filename=upload.filename,
        mime_type=upload.mime_type,
        uploaded_by=author,
    )
    recipients = resolve_recipients(
        chosen=final_opinion.get("recipients") or [],
        typed_names=final_opinion.get("recipient_names") or [],
    )
    submission = create_submission(
        matter=matter,
        title=matter.title,
        actor=author,
        recipients=recipients,
    )
    select_final_evidence(submission=submission, version=version, actor=author)
    return mark_submission_sent(
        submission=submission,
        actor=author,
        sent_at=final_opinion.get("sent_at"),
        sent_at_precision=SentAtPrecision.DATE,
    )


def _closure_commencement(*, matter: Matter, author: Any, effective: dict[str, Any]) -> Any:
    """When the result took effect, recorded once.

    A closure that reports a win states a commencement date, and the department
    may already hold that fact — somebody entered it on `Teema andmed` when the
    act was published. Adding a second identical row because the file is now
    being closed would double every commencement in the register, so an
    equivalent active exact date is returned rather than repeated (§13).
    """
    from app.intelligence.enums import EffectiveDateKind, FactStatus
    from app.intelligence.models import MatterEffectiveDate
    from app.intelligence.services import add_effective_date

    date_value = effective["date_value"]
    existing = MatterEffectiveDate.objects.filter(
        matter=matter,
        status=FactStatus.ACTIVE,
        kind=EffectiveDateKind.KNOWN_DATE,
        date_precision=DatePrecision.EXACT,
        date_value=date_value,
        # A row taken off the file is not a fact the file states any more, so
        # it cannot stand in for the commencement this closure records
        # (docs/adr/0102, ENG-047).
        removed_at__isnull=True,
    ).first()
    if existing is not None:
        return existing

    return add_effective_date(
        matter=matter,
        actor=author,
        kind=EffectiveDateKind.KNOWN_DATE,
        date_value=date_value,
        period_end=effective["period_end"],
        date_precision=DatePrecision.EXACT,
    )


def _apply_closure(
    *,
    matter: Matter,
    author: Any,
    result: ComposerResult,
    closure: dict[str, Any],
) -> None:
    """Finish the Matter, with whatever the person recorded alongside it.

    Split out because the closure half of a composer save is four decisions,
    not one, and each has a rule of its own:

    * the **final opinion** is a canonical ``Submission`` or it is nothing —
      created, given the exact evidence that went out, and marked sent through
      the existing workflow, which refuses to mark anything sent without a
      final version and refuses evidence less restricted than the submission
      itself. A PDF is not an opinion and a filename is not a sent date
      (Teema redesign §17, §20).
    * the **work victory** goes through the same door the Matter page's own
      control already uses, so this feature broadens nobody's authorization and
      the department head's review of *imported* candidates is untouched
      (Teema redesign §18). Its wording is the composer body: one narrative per
      save, never a second box asking the same question (closing redesign §12).
    * the **commencement date** is a `MatterEffectiveDate`, because that is
      what the domain calls when something took effect. The victory's
      `period_date` is a reporting period and is left alone (§13).
    * **closure itself** runs last, and ends the open next action through the
      existing lifecycle service rather than deleting it.
    """
    from app.intelligence.services import add_confirmed_work_victory

    final_opinion = closure.get("final_opinion")
    if final_opinion:
        result.submission = _closure_final_opinion(
            matter=matter, author=author, final_opinion=final_opinion
        )

    victory = closure.get("work_victory")
    if victory:
        result.work_victory = add_confirmed_work_victory(matter=matter, actor=author, **victory)

    effective = closure.get("effective_date")
    if effective:
        result.effective_date = _closure_commencement(
            matter=matter, author=author, effective=effective
        )

    close_matter(
        matter=matter,
        disposition=closure["disposition"],
        actor=author,
        reason=closure.get("reason", ""),
        successor=closure.get("successor"),
    )
    result.closed = True
