"""Named use cases for Matters.

Stage 0 implements only what the foundational schema needs to be provably
correct: reference allocation, creation, and visibility changes that propagate
to children. Assignment, stage change, next action, submissions and closure are
Stage-1 work and belong here too when they arrive.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.services import record_change_event
from app.core.enums import Visibility
from app.core.errors import DomainError
from app.matters.enums import MatterOrigin, RecordMode
from app.matters.models import Matter, MatterReferenceSequence


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
        raise DomainError("A Matter requires a title.")

    year_number: tuple[int, int] | None = None
    if assign_reference:
        year_number = allocate_matter_reference(reference_year)

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
        raise DomainError(f"Unknown visibility {visibility!r}.")

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
