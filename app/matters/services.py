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
    """Change a Matter's visibility and re-derive every child record.

    Children may be more restrictive than the Matter, so tightening the Matter
    tightens everything, while relaxing it leaves individually restricted
    children restricted.
    """
    if visibility not in Visibility.values:
        raise DomainError(f"Unknown visibility {visibility!r}.")

    previous = matter.visibility
    if previous == visibility:
        return matter

    matter.visibility = visibility
    matter.save(update_fields=["visibility", "updated_at"])
    propagate_visibility_to_children(matter)

    record_change_event(
        event_type=ChangeEventType.MATTER_VISIBILITY_CHANGED,
        matter=matter,
        actor=actor,
        obj=matter,
        payload={"from": previous, "to": visibility},
    )
    return matter


def propagate_visibility_to_children(matter: Matter) -> None:
    """Re-derive ``effective_visibility`` on every child of this Matter.

    Saving each child rather than issuing a bulk UPDATE keeps the derivation in
    one place. Child volumes per Matter are small; if that ever stops being
    true, the derivation moves into SQL, not into a second rule.
    """
    for document in matter.documents.all():
        document.matter = matter
        document.save(update_fields=["effective_visibility", "updated_at"])
