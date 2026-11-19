"""Proposed senders and Valdkonnad, and the person who confirms them (ADR 0215).

Statistika's «Saatja järgi» and «Valdkonna järgi» read a Teema's own fields.
Those were empty on every Teema in November 2026, so the titles were reviewed
once and each Teema got a proposal (`ClassificationSuggestion`). Nothing here
writes a Teema field on its own: a lawyer confirms, and the confirmation goes
through the same audited setters as the Teema page (`set_policy_areas`,
`set_organisations`), adding to whatever the Teema already holds.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from app.core.errors import DomainError
from app.matters.models import Matter
from app.matters.services import set_organisations, set_policy_areas
from app.organisations.models import Organisation
from app.reporting.models import ClassificationSuggestion, SuggestionStatus
from app.taxonomy.models import PolicyArea
from app.taxonomy.vocabulary import selectable_policy_areas

CONFIDENCE_LABELS = {"h": "kindel", "m": "tõenäoline", "l": "oletus"}


@dataclass(frozen=True)
class LoadReport:
    created: int
    already: int
    missing_matter: int
    unknown_sender: tuple[str, ...]
    unknown_area: tuple[str, ...]
    #: Areas that exist but are no longer offered for filing (`selectable_policy_areas`):
    #: left out of the proposal and reported, never stored to be dropped later (QA26-DATA-02).
    retired_area: tuple[str, ...] = ()


def load(data: dict[str, Any], *, dry_run: bool = False) -> LoadReport:
    """Store one proposal per Teema from a reviewed file. Idempotent.

    A Teema that already has a proposal keeps it, whatever its state: a second
    load never reopens a decision somebody made. A sender or area the file names
    that this database does not have is reported, and that part is left out.
    """
    source = str(data.get("source") or "")[:64]
    if not source:
        raise DomainError("Failil puudub allikas.")
    rows = list(data.get("suggestions") or [])
    organisations = {org.name: org for org in Organisation.objects.all()}
    areas = set(PolicyArea.objects.values_list("key", flat=True))
    selectable = {area.key for area in selectable_policy_areas()}
    known = {
        str(pk)
        for pk in Matter.objects.filter(pk__in=[row["matter"] for row in rows]).values_list(
            "pk", flat=True
        )
    }
    taken = {
        str(pk)
        for pk in ClassificationSuggestion.objects.filter(matter_id__in=known).values_list(
            "matter_id", flat=True
        )
    }
    unknown_sender: set[str] = set()
    unknown_area: set[str] = set()
    retired_area: set[str] = set()
    created: list[ClassificationSuggestion] = []
    missing = already = 0
    for row in rows:
        matter_id = str(row["matter"])
        if matter_id not in known:
            missing += 1
            continue
        if matter_id in taken:
            already += 1
            continue
        keys = [key for key in row.get("areas") or [] if key]
        unknown_area.update(key for key in keys if key not in areas)
        retired_area.update(key for key in keys if key in areas and key not in selectable)
        name = row.get("sender")
        sender = organisations.get(name) if name else None
        if name and sender is None:
            unknown_sender.add(name)
        confidence = row.get("confidence")
        if confidence not in CONFIDENCE_LABELS:
            raise DomainError(f"Tundmatu kindlus: {confidence!r}.")
        created.append(
            ClassificationSuggestion(
                matter_id=matter_id,
                policy_area_keys=[key for key in keys if key in selectable],
                sender=sender,
                confidence=confidence,
                source=source,
            )
        )
        taken.add(matter_id)
    if not dry_run:
        ClassificationSuggestion.objects.bulk_create(created, batch_size=500)
    return LoadReport(
        created=len(created),
        already=already,
        missing_matter=missing,
        unknown_sender=tuple(sorted(unknown_sender)),
        unknown_area=tuple(sorted(unknown_area)),
        retired_area=tuple(sorted(retired_area)),
    )


def pending_for(user: Any) -> QuerySet[ClassificationSuggestion]:
    """Open proposals on Teemad the reader may see, newest Teema year first."""
    return (
        ClassificationSuggestion.objects.filter(
            status=SuggestionStatus.PENDING,
            matter__in=Matter.objects.visible_to(user),
        )
        .select_related("matter", "sender")
        .order_by("-matter__reporting_year", "matter__title", "pk")
    )


def own_pending(user: Any, pk: Any) -> ClassificationSuggestion | None:
    return pending_for(user).filter(pk=pk).first()


def offered_areas() -> list[PolicyArea]:
    return list(selectable_policy_areas())


#: Confirming a proposal that would write nothing is refused, not recorded as done
#: (QA26-DATA-02): its area is no longer offered and it names no sender.
NOTHING_TO_CONFIRM = (
    "Ettepanekus pole midagi kinnitada: vali valdkond või saatja, või jäta ettepanek vahele."
)


def retired_area_names(suggestion: ClassificationSuggestion) -> list[str]:
    """The proposal's areas that exist but are no longer offered, by name."""
    offered = {area.key for area in selectable_policy_areas()}
    keys = [key for key in suggestion.policy_area_keys if key not in offered]
    if not keys:
        return []
    return list(
        PolicyArea.objects.filter(key__in=keys)
        .order_by("name_et")
        .values_list("name_et", flat=True)
    )


def offered_senders() -> list[Organisation]:
    """The bodies a proposal may name: ministries, Riigikogu, the Government, EU institutions."""
    return list(
        Organisation.objects.filter(
            organisation_type__in=["MINISTRY", "PARLIAMENT", "GOVERNMENT", "EU_INSTITUTION"],
        ).order_by("organisation_type", "name")
    )


@transaction.atomic
def accept(
    suggestion: ClassificationSuggestion,
    *,
    actor: Any,
    areas: Sequence[PolicyArea] | None = None,
    sender: Organisation | str | None = "proposed",
) -> ClassificationSuggestion:
    """Confirm a proposal, as proposed or as the person corrected it.

    ``areas=None`` takes the proposal's areas; ``sender="proposed"`` takes the
    proposal's sender, and ``None`` means "no sender". Both are *added* to what
    the Teema already holds: a value somebody set by hand is never removed here.
    """
    locked = ClassificationSuggestion.objects.select_for_update().get(pk=suggestion.pk)
    if locked.status != SuggestionStatus.PENDING:
        raise DomainError("See ettepanek on juba otsustatud.")
    matter = locked.matter
    if areas is None:
        by_key = {area.key: area for area in selectable_policy_areas()}
        areas = [by_key[key] for key in locked.policy_area_keys if key in by_key]
    if sender == "proposed":
        sender = locked.sender
    chosen_areas = list(areas)
    if not chosen_areas and not isinstance(sender, Organisation):
        # Nothing would reach the Teema: ACCEPTED here would hide a proposal
        # whose only area was retired, and «Kinnita kõik» would count it.
        raise DomainError(NOTHING_TO_CONFIRM)
    if chosen_areas:
        current = list(matter.policy_areas.all())
        held = {area.pk for area in current}
        set_policy_areas(
            matter=matter,
            policy_areas=[*current, *(area for area in chosen_areas if area.pk not in held)],
            actor=actor,
        )
    if isinstance(sender, Organisation):
        current_senders = list(matter.source_organisations.all())
        if sender.pk not in {org.pk for org in current_senders}:
            set_organisations(
                matter=matter, source_organisations=[*current_senders, sender], actor=actor
            )
    locked.status = SuggestionStatus.ACCEPTED
    locked.decided_by = actor
    locked.decided_at = timezone.now()
    locked.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
    return locked


@transaction.atomic
def dismiss(suggestion: ClassificationSuggestion, *, actor: Any) -> ClassificationSuggestion:
    locked = ClassificationSuggestion.objects.select_for_update().get(pk=suggestion.pk)
    if locked.status != SuggestionStatus.PENDING:
        raise DomainError("See ettepanek on juba otsustatud.")
    locked.status = SuggestionStatus.DISMISSED
    locked.decided_by = actor
    locked.decided_at = timezone.now()
    locked.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
    return locked


def accept_many(suggestions: Iterable[ClassificationSuggestion], *, actor: Any) -> int:
    """Confirm each proposal as proposed; one already decided is skipped."""
    done = 0
    for suggestion in suggestions:
        try:
            accept(suggestion, actor=actor)
        except DomainError:
            continue
        done += 1
    return done
