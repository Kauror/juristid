"""Named use cases for Matters.

Every business state change lives here. Views, forms and templates call these
functions; they never write model fields themselves. That is what makes the
audit trail complete, the invariants testable, and the same operation reusable
later from an importer or a scheduled job (master specification 12.4).
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.services import record_change_event
from app.core.enums import Visibility, validate_visibility_override
from app.core.errors import DomainError
from app.core.richtext import excerpt, is_empty, sanitize_entry_html
from app.documents.enums import DocumentRole
from app.documents.services import add_evidence_version, create_document
from app.documents.uploads import read_upload
from app.matters.entry_enums import EntryKind
from app.matters.enums import DataQualityTier, MatterOrigin, RecordMode
from app.matters.models import Entry, EntryRevision, Matter, MatterReferenceSequence
from app.workflow.enums import Disposition, Track
from app.workflow.services import end_open_action_for_closure, set_next_action

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
    **extra: Any,
) -> Matter:
    """Create a Matter. Only the title is required (specification 3.8)."""
    if not title.strip():
        raise DomainError("Teema vajab pealkirja.")
    if visibility not in Visibility.values:
        raise DomainError(f"Tundmatu nähtavus {visibility!r}.")
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

    matter = Matter.objects.create(
        title=title.strip(),
        owner=owner,
        record_mode=record_mode,
        origin=origin,
        visibility=visibility,
        reference_year=year_number[0] if year_number else None,
        reference_number=year_number[1] if year_number else None,
        reporting_year=extra.pop("reporting_year", year_number[0] if year_number else None),
        **extra,
    )
    if policy_areas:
        matter.policy_areas.set(policy_areas)

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
        },
    )
    return matter


@transaction.atomic
def set_matter_visibility(*, matter: Matter, visibility: str, actor: Any = None) -> Matter:
    """Change a Matter's visibility, audited.

    Child records need no update: their effective visibility is derived from
    this value every time it is read, so tightening the Matter tightens every
    child immediately and relaxing it leaves individually restricted children
    restricted. Nothing here can go stale, and a write that bypasses this
    function changes what children are visible just as correctly — it only
    misses the audit record (docs/adr/0005).
    """
    if visibility not in Visibility.values:
        raise DomainError(f"Tundmatu nähtavus {visibility!r}.")

    previous = matter.visibility
    if previous == visibility:
        return matter

    matter.visibility = visibility
    matter.save(update_fields=["visibility", "updated_at"])

    record_change_event(
        event_type=ChangeEventType.MATTER_VISIBILITY_CHANGED,
        matter=matter,
        actor=actor,
        obj=matter,
        payload={"from": previous, "to": visibility},
    )
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

    record_change_event(
        event_type=ChangeEventType.MATTER_ASSIGNED,
        matter=matter,
        actor=actor,
        obj=matter,
        summary=getattr(owner, "display_name", "") or "",
        payload={
            "from_name": getattr(previous, "display_name", None),
            "to_name": getattr(owner, "display_name", None),
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


@transaction.atomic
def set_organisations(
    *,
    matter: Matter,
    source_organisation: Any = _UNSET,
    addressee_organisation: Any = _UNSET,
    actor: Any = None,
) -> Matter:
    """Set who the Matter came from and who it is addressed to.

    These are two different facts and are never unified. The register's own
    history is the argument: the counterparty column changed meaning from
    `KELLELT` to `KELLELE` in 2020, so merging them on name similarity would
    silently invert the direction of a decade of records
    (master specification 2.1, 19.3).
    """
    changed: dict[str, Any] = {}
    fields: list[str] = []

    if source_organisation is not _UNSET and source_organisation != matter.source_organisation:
        changed["source_from"] = getattr(matter.source_organisation, "name", None)
        changed["source_to"] = getattr(source_organisation, "name", None)
        matter.source_organisation = source_organisation
        fields.append("source_organisation")

    if (
        addressee_organisation is not _UNSET
        and addressee_organisation != matter.addressee_organisation
    ):
        changed["addressee_from"] = getattr(matter.addressee_organisation, "name", None)
        changed["addressee_to"] = getattr(addressee_organisation, "name", None)
        matter.addressee_organisation = addressee_organisation
        fields.append("addressee_organisation")

    if not fields:
        return matter

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
def close_matter(
    *, matter: Matter, disposition: str, actor: Any = None, reason: str = ""
) -> Matter:
    """Stop active work on the Matter, for a stated reason.

    Closure answers "why is Koda no longer working on this", which is a
    different question from where the external process stands. An act can enter
    into force with the file still open, and a file can close while the
    procedure continues elsewhere.
    """
    if disposition not in Disposition.values:
        raise DomainError(f"Tundmatu lõpetamise põhjus {disposition!r}.")

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
    matter.save(
        update_fields=[
            "is_open",
            "disposition",
            "disposition_reason",
            "closed_at",
            "closed_by",
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
        payload={"disposition": disposition},
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
    matter.save(
        update_fields=[
            "is_open",
            "disposition",
            "disposition_reason",
            "closed_at",
            "closed_by",
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
) -> tuple[Entry | None, Any]:
    """The unified composer: one save, one transaction.

    This is the adoption feature. A routine update today means editing an Excel
    row and then writing the same thing into a OneNote page; here it is one box,
    one optional change to `Järgmiseks`, and one save.

    Atomicity is the substance of it, not a technicality. If the entry saved and
    the action did not, the lawyer would believe both landed while the work
    queue quietly disagreed with the record.
    """
    if not body.strip() and not next_action and attachment is None:
        raise DomainError("Täida sissekanne või järgmiseks.")

    entry = None
    if body.strip():
        entry = add_entry(
            matter=matter,
            body=body,
            author=author,
            kind=kind,
            occurred_at=occurred_at,
            organisation=organisation,
        )

    if attachment is not None:
        # An attachment is evidence like any other: same immutability, same
        # checksum, same provenance. It is captured inside this transaction, so
        # a failed save leaves neither the note nor the file behind.
        upload = read_upload(attachment)
        document = create_document(
            matter=matter,
            title=upload.filename,
            role=DocumentRole.OTHER,
            created_by=author,
        )
        add_evidence_version(
            document=document,
            content=upload.content,
            original_filename=upload.filename,
            mime_type=upload.mime_type,
            uploaded_by=author,
        )

    action = None
    if next_action:
        action = set_next_action(matter=matter, actor=author, **next_action)

    return entry, action
