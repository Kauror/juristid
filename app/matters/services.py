"""Named use cases for Matters.

Every business state change lives here. Views, forms and templates call these
functions; they never write model fields themselves. That is what makes the
audit trail complete, the invariants testable, and the same operation reusable
later from an importer or a scheduled job (master specification 12.4).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django.db import transaction
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
    DataQualityTier,
    EngagementKind,
    MatterDataClass,
    MatterOrigin,
    RecordMode,
    TagAssignmentSource,
)
from app.matters.locks import lock_matter_for_evidence_integrity
from app.matters.models import (
    Entry,
    EntryRevision,
    Matter,
    MatterEngagement,
    MatterPersonalNote,
    MatterReferenceSequence,
    TagAssignment,
)
from app.submissions.models import Submission
from app.workflow.enums import ActionStatus, Disposition, Track
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

    policy_areas = extra.pop("policy_areas", None)
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
        },
    )
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
    """
    previous = matter.owner
    if previous == owner:
        return matter

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

    if source_organisations is not _UNSET:
        proposed = normalize_source_organisations(source_organisations)
        current = list(matter.source_organisations.all())
        if {organisation.pk for organisation in current} != {
            organisation.pk for organisation in proposed
        }:
            changed["source_from"] = _sender_payload(current)
            changed["source_to"] = _sender_payload(proposed)
            new_senders = proposed

    if (
        addressee_organisation is not _UNSET
        and addressee_organisation != matter.addressee_organisation
    ):
        changed["addressee_from"] = getattr(matter.addressee_organisation, "name", None)
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
    if author is None or not getattr(author, "is_authenticated", False):
        return ""
    record = MatterPersonalNote.objects.filter(matter=matter, author=author).first()
    return record.body if record is not None else ""


def save_personal_note(*, matter: Matter, author: Any, body: str) -> MatterPersonalNote:
    """Autosave a private draft.

    Writes no `ChangeEvent` on purpose, and is the only write in the product
    that does not. It is not a business change: nothing downstream reads it, no
    statistic counts it, it never appears on the timeline and it is not
    evidence. Recording every autosave of somebody's scratch paper as
    authoritative history would bury the history it sits beside
    (Teema redesign §22.4).
    """
    if author is None or not getattr(author, "is_authenticated", False):
        raise DomainError("Märkmeid saab salvestada ainult sisselogitud kasutaja.")
    record, _created = MatterPersonalNote.objects.update_or_create(
        matter=matter,
        author=author,
        defaults={"body": body or ""},
    )
    return record


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
    chosen = list(policy_areas)
    before = {area.pk for area in matter.policy_areas.all()}
    after = {area.pk for area in chosen}
    if before == after:
        return matter

    matter.policy_areas.set(chosen)
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


def normalize_engagement_url(value: str | None) -> str:
    """Trim it, allow it to be empty, and refuse anything not http(s)."""
    from urllib.parse import urlsplit

    url = (value or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    if parts.scheme.lower() not in ENGAGEMENT_URL_SCHEMES:
        raise DomainError("Link peab algama http:// või https:// aadressiga.")
    if not parts.netloc:
        raise DomainError("Link peab sisaldama veebiaadressi.")
    return url


def _engagement_kind(value: str) -> str:
    if value not in EngagementKind.values:
        raise DomainError(f"Tundmatu kaasamise liik {value!r}.")
    return value


@transaction.atomic
def add_engagement(
    *,
    matter: Matter,
    kind: str,
    title: str,
    url: str = "",
    note: str = "",
    occurred_on: Any = None,
    actor: Any = None,
) -> MatterEngagement:
    """Record one act of asking members or stakeholders for input.

    Writes no `Entry`. One action must not become two records — a structured
    engagement and a narrative note saying the same thing — because the day they
    disagree there is no way to tell which was meant (brief 45).
    """
    clean_title = title.strip()
    if not clean_title:
        raise DomainError("Kaasamisel peab olema pealkiri.")

    engagement = MatterEngagement.objects.create(
        matter=matter,
        kind=_engagement_kind(kind),
        title=clean_title[:500],
        url=normalize_engagement_url(url),
        note=note.strip(),
        occurred_on=occurred_on,
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
            "has_url": bool(engagement.url),
        },
    )
    return engagement


@transaction.atomic
def update_engagement(
    *,
    engagement: MatterEngagement,
    kind: str = _UNSET,
    title: str = _UNSET,
    url: Any = _UNSET,
    note: Any = _UNSET,
    occurred_on: Any = _UNSET,
    actor: Any = None,
) -> MatterEngagement:
    """Correct an engagement, and say nothing when nothing changed.

    The payload names the fields that moved and carries values only for the
    small ones. A note can run to paragraphs, and copying every version of it
    into the audit table would turn the history into a second, worse copy of
    the notes themselves (brief 26).
    """
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
    if note is not _UNSET:
        proposed["note"] = (note or "").strip()
    if occurred_on is not _UNSET:
        proposed["occurred_on"] = occurred_on

    changed = [field for field, value in proposed.items() if getattr(engagement, field) != value]
    if not changed:
        return engagement

    payload: dict[str, Any] = {"fields": sorted(changed)}
    if "kind" in changed:
        payload["kind_from"] = engagement.kind
        payload["kind_to"] = proposed["kind"]
    if "occurred_on" in changed:
        payload["occurred_on_from"] = (
            engagement.occurred_on.isoformat() if engagement.occurred_on else None
        )
        payload["occurred_on_to"] = (
            proposed["occurred_on"].isoformat() if proposed["occurred_on"] else None
        )

    for field in changed:
        setattr(engagement, field, proposed[field])
    engagement.save(update_fields=[*changed, "updated_at"])
    record_change_event(
        event_type=ChangeEventType.ENGAGEMENT_CHANGED,
        matter=engagement.matter,
        actor=actor,
        obj=engagement,
        summary=engagement.title[:200],
        payload=payload,
    )
    return engagement


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

    # The same lock set_next_action takes, in the same order. Whichever
    # transaction reaches the Matter row first wins: a closure that lands first
    # makes the other call refuse, and a next action that lands first is
    # cancelled by the closure. Neither ordering can leave a closed Matter
    # carrying an open instruction (docs/adr/0011).
    locked = Matter.objects.select_for_update().get(pk=matter.pk)
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


@transaction.atomic
def reopen_matter(*, matter: Matter, actor: Any = None, reason: str = "") -> Matter:
    if matter.is_open:
        raise DomainError("Teema on juba avatud.")

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


@transaction.atomic
def edit_entry(*, entry: Entry, body: str, actor: Any = None) -> Entry:
    """Change an entry's text, keeping what it said before.

    Correcting a typo should not require a correction note, but the earlier
    wording is preserved so an edit cannot silently rewrite the record.
    """
    clean_body = sanitize_entry_html(body)
    if is_empty(clean_body):
        raise DomainError("Sissekanne vajab sisu.")

    # Lock the row and re-read it before deciding anything. Two people editing
    # the same entry at once would otherwise both compute the same revision
    # number from a stale copy: one revision would collide, and one version of
    # the wording would be lost. The second writer waits, then edits whatever is
    # current by then.
    locked = Entry.objects.select_for_update().get(pk=entry.pk)
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
    """
    wants_something = bool(
        body.strip()
        or next_action
        or attachment is not None
        or important_date
        or engagement
        or closure
    )
    if not wants_something:
        raise DomainError("Täida sissekanne või vali, mida veel salvestada.")

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

    * the **final opinion** is a canonical ``Submission`` or it is nothing. It
      is created, given the exact evidence that went out, and marked sent
      through the existing workflow — which refuses to mark anything sent
      without a final version, refuses evidence belonging to another Matter and
      refuses evidence less restricted than the submission itself. A PDF is not
      an opinion and a filename is not a sent date (Teema redesign §17, §20).
    * the **work victory** goes through the same door the Matter page's own
      control already uses, so this feature broadens nobody's authorization and
      the department head's review of *imported* candidates is untouched
      (Teema redesign §18).
    * **closure itself** runs last, and ends the open next action through the
      existing lifecycle service rather than deleting it.
    """
    from app.intelligence.services import add_confirmed_work_victory
    from app.submissions.services import (
        create_submission,
        mark_submission_sent,
        select_final_evidence,
    )

    final_opinion = closure.get("final_opinion")
    if final_opinion:
        submission = create_submission(
            matter=matter,
            title=final_opinion["title"],
            actor=author,
            recipients=list(final_opinion.get("recipients") or []),
            channel=final_opinion.get("channel", ""),
            reference=final_opinion.get("reference", ""),
        )
        select_final_evidence(
            submission=submission,
            version=final_opinion["final_version"],
            actor=author,
        )
        result.submission = mark_submission_sent(
            submission=submission,
            actor=author,
            sent_at=final_opinion.get("sent_at"),
            channel=final_opinion.get("channel", ""),
            reference=final_opinion.get("reference", ""),
        )

    victory = closure.get("work_victory")
    if victory:
        result.work_victory = add_confirmed_work_victory(matter=matter, actor=author, **victory)

    close_matter(
        matter=matter,
        disposition=closure["disposition"],
        actor=author,
        reason=closure.get("reason", ""),
        successor=closure.get("successor"),
    )
    result.closed = True
