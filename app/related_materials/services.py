"""The six human decisions of «Seotud materjalid», and the only writers of them.

Nothing else in the application writes a relation, a background selection or a
dismissal. The recommendation engine computes and forgets; the views resolve
who may see what and hand the resolved objects here; these functions decide
what a click *means* and record it (docs/adr/0061).

Every function is idempotent and atomic. Two colleagues pressing «Seo teemaga»
at the same moment produce one row, because the pair is canonicalised before
the database is asked and the database holds the uniqueness — `get_or_create`
retries its read on the constraint violation rather than surfacing it to a
person who did nothing wrong. A second click on anything here changes nothing
and says so through the ``created`` flag.

Confirmed relations and background selections are business state: each records
its actor and time on the row and writes a `ChangeEvent` in the same
transaction, once per Matter it concerns. A dismissal keeps its actor and time
on its own row and writes no event — it is a preference about what to suggest,
not a fact about the file, and the ordinary history is not where it belongs.

What these functions never do: create a `Submission`, move one between Matters,
touch evidence, create or remove an `OpinionArchiveMatterLink`, or turn a
removed relation into a dismissal. Those are different decisions, and a person
who withdraws a relation has not said the candidate is unrelated.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.services import record_change_event
from app.core.errors import DomainError
from app.legacy_import.opinion_binary import OpinionArchiveBinary
from app.legacy_import.opinion_search_models import OpinionArchiveSearchDocument
from app.matters.models import Matter
from app.related_materials.models import (
    MatterBackgroundMaterial,
    MatterRelation,
    RelatedSuggestionDismissal,
)
from app.submissions.models import Submission

SOURCE_SUBMISSION = "SUBMISSION"
SOURCE_ARCHIVE = "ARCHIVE"


def _require_person(actor: Any) -> Any:
    """A signed-in human. The shared-gate sentinel has no key and cannot author."""
    if actor is None or getattr(actor, "pk", None) is None:
        raise DomainError("Seotud materjalide muutmine vajab sisse loginud kasutajat.")
    if not getattr(actor, "is_authenticated", False):
        raise DomainError("Seotud materjalide muutmine vajab sisse loginud kasutajat.")
    return actor


def canonical_pair(first: Matter, second: Matter) -> tuple[Matter, Matter]:
    """The one order a pair is stored in: the smaller primary key first.

    UUIDs compare as 128-bit integers in Python and byte-wise in PostgreSQL,
    which for a big-endian encoding is the same order — so the check constraint
    `related_relation_pair_is_canonical` agrees with this function.
    """
    if first.pk == second.pk:
        raise DomainError("Teemat ei saa siduda iseendaga.")
    return (first, second) if first.pk < second.pk else (second, first)


def _matter_label(matter: Matter) -> str:
    reference = matter.display_reference
    return f"{reference} · {matter.title}"[:200] if reference else matter.title[:200]


# ---------------------------------------------------------------------------
# Related Matters
# ---------------------------------------------------------------------------


@transaction.atomic
def link_related_matters(
    *, matter: Matter, other: Matter, actor: Any, note: str = ""
) -> tuple[MatterRelation, bool]:
    """Record that two Matters are related. Symmetric, idempotent.

    Both Matters get a history line, because the relation is about both of
    them and a reader of either file should find it. A dismissal in either
    direction is cleared: the person has just said the opposite.
    """
    person = _require_person(actor)
    first, second = canonical_pair(matter, other)
    relation, created = MatterRelation.objects.get_or_create(
        matter_a=first,
        matter_b=second,
        defaults={"linked_by": person, "linked_at": timezone.now(), "note": note.strip()},
    )
    if not created:
        return relation, False

    RelatedSuggestionDismissal.objects.filter(
        Q(matter=first, candidate_matter=second) | Q(matter=second, candidate_matter=first)
    ).delete()
    for subject, counterpart in ((first, second), (second, first)):
        record_change_event(
            event_type=ChangeEventType.MATTER_RELATION_ADDED,
            matter=subject,
            actor=person,
            obj=relation,
            summary=_matter_label(counterpart),
            payload={
                "related_matter_id": str(counterpart.pk),
                "related_reference": counterpart.display_reference,
            },
        )
    return relation, True


@transaction.atomic
def unlink_related_matters(*, matter: Matter, other: Matter, actor: Any) -> bool:
    """Withdraw a relation. Returns whether there was one to withdraw.

    Deliberately does **not** record a dismissal. Removing a relation says "we
    no longer assert this"; a dismissal says "do not suggest it". The second is
    a separate click.
    """
    person = _require_person(actor)
    first, second = canonical_pair(matter, other)
    relation = MatterRelation.objects.filter(matter_a=first, matter_b=second).first()
    if relation is None:
        return False
    relation_id = relation.pk
    relation.delete()
    for subject, counterpart in ((first, second), (second, first)):
        record_change_event(
            event_type=ChangeEventType.MATTER_RELATION_REMOVED,
            matter=subject,
            actor=person,
            summary=_matter_label(counterpart),
            payload={
                "relation_id": str(relation_id),
                "related_matter_id": str(counterpart.pk),
                "related_reference": counterpart.display_reference,
            },
        )
    return True


# ---------------------------------------------------------------------------
# Background material
# ---------------------------------------------------------------------------


def archive_title(binary: OpinionArchiveBinary) -> str:
    """What the archive projection calls a letter, or nothing."""
    return (
        OpinionArchiveSearchDocument.objects.filter(binary=binary)
        .values_list("title", flat=True)
        .first()
        or ""
    )


@transaction.atomic
def add_background_submission(
    *, matter: Matter, submission: Submission, actor: Any, note: str = ""
) -> tuple[MatterBackgroundMaterial, bool]:
    """Select a canonical opinion on another Matter as background for this one.

    The Submission is read, never written: its Matter, its date, its
    recipients, its evidence and its status are exactly what they were.
    """
    person = _require_person(actor)
    if submission.matter_id == matter.pk:
        raise DomainError("Teema enda arvamus ei ole selle teema taustmaterjal.")
    row, created = MatterBackgroundMaterial.objects.get_or_create(
        matter=matter,
        submission=submission,
        defaults={"added_by": person, "added_at": timezone.now(), "note": note.strip()},
    )
    if not created:
        return row, False
    RelatedSuggestionDismissal.objects.filter(
        matter=matter, candidate_submission=submission
    ).delete()
    record_change_event(
        event_type=ChangeEventType.BACKGROUND_MATERIAL_ADDED,
        matter=matter,
        actor=person,
        obj=row,
        summary=submission.title[:200],
        payload={
            "source": SOURCE_SUBMISSION,
            "submission_id": str(submission.pk),
            "source_matter_id": str(submission.matter_id),
        },
    )
    return row, True


@transaction.atomic
def add_background_archive_material(
    *, matter: Matter, binary: OpinionArchiveBinary, actor: Any, note: str = ""
) -> tuple[MatterBackgroundMaterial, bool]:
    """Select a held archive letter as background for this Matter.

    Not an `OpinionArchiveMatterLink`. That table says a letter *concerns* a
    Matter and is written by the reconciliation and its reviewers; this says
    somebody found the letter useful here, which is a weaker and different
    thing. Zero rows of the other table are touched (docs/adr/0061 §3).
    """
    person = _require_person(actor)
    row, created = MatterBackgroundMaterial.objects.get_or_create(
        matter=matter,
        archive_binary=binary,
        defaults={"added_by": person, "added_at": timezone.now(), "note": note.strip()},
    )
    if not created:
        return row, False
    RelatedSuggestionDismissal.objects.filter(
        matter=matter, candidate_archive_binary=binary
    ).delete()
    record_change_event(
        event_type=ChangeEventType.BACKGROUND_MATERIAL_ADDED,
        matter=matter,
        actor=person,
        obj=row,
        summary=archive_title(binary)[:200],
        payload={"source": SOURCE_ARCHIVE, "archive_binary_id": str(binary.pk)},
    )
    return row, True


@transaction.atomic
def remove_background_material(
    *,
    matter: Matter,
    actor: Any,
    submission: Submission | None = None,
    archive_binary: OpinionArchiveBinary | None = None,
) -> bool:
    """Withdraw a background selection. Returns whether there was one.

    Removes this table's row and nothing else: an `OpinionArchiveMatterLink`
    that happens to name the same letter is a different claim and stays.
    """
    person = _require_person(actor)
    if (submission is None) == (archive_binary is None):
        raise DomainError("Taustmaterjalil peab olema täpselt üks allikas.")
    rows = MatterBackgroundMaterial.objects.filter(matter=matter)
    if submission is not None:
        rows = rows.filter(submission=submission)
        summary = submission.title[:200]
        payload: dict[str, Any] = {"source": SOURCE_SUBMISSION, "submission_id": str(submission.pk)}
    elif archive_binary is not None:
        rows = rows.filter(archive_binary=archive_binary)
        summary = archive_title(archive_binary)[:200]
        payload = {"source": SOURCE_ARCHIVE, "archive_binary_id": str(archive_binary.pk)}
    else:  # pragma: no cover - refused above
        raise DomainError("Taustmaterjalil peab olema täpselt üks allikas.")
    row = rows.first()
    if row is None:
        return False
    payload["background_id"] = str(row.pk)
    row.delete()
    record_change_event(
        event_type=ChangeEventType.BACKGROUND_MATERIAL_REMOVED,
        matter=matter,
        actor=person,
        summary=summary,
        payload=payload,
    )
    return True


# ---------------------------------------------------------------------------
# Dismissals
# ---------------------------------------------------------------------------


def _one_candidate(
    *,
    candidate_matter: Matter | None,
    candidate_submission: Submission | None,
    candidate_archive_binary: OpinionArchiveBinary | None,
) -> dict[str, Any]:
    given = [
        value
        for value in (candidate_matter, candidate_submission, candidate_archive_binary)
        if value is not None
    ]
    if len(given) != 1:
        raise DomainError("Soovitusel peab olema täpselt üks kandidaat.")
    return {
        "candidate_matter": candidate_matter,
        "candidate_submission": candidate_submission,
        "candidate_archive_binary": candidate_archive_binary,
    }


@transaction.atomic
def dismiss_related_suggestion(
    *,
    matter: Matter,
    actor: Any,
    candidate_matter: Matter | None = None,
    candidate_submission: Submission | None = None,
    candidate_archive_binary: OpinionArchiveBinary | None = None,
) -> tuple[RelatedSuggestionDismissal, bool]:
    """«Ei ole seotud»: stop suggesting this candidate for this Matter.

    Matter-level and durable, so the whole team stops seeing it. Not a deletion
    of anything, and reversible through :func:`restore_related_suggestion`.
    """
    person = _require_person(actor)
    candidate = _one_candidate(
        candidate_matter=candidate_matter,
        candidate_submission=candidate_submission,
        candidate_archive_binary=candidate_archive_binary,
    )
    if candidate_matter is not None and candidate_matter.pk == matter.pk:
        raise DomainError("Teema ei saa olla iseenda soovitus.")
    return RelatedSuggestionDismissal.objects.get_or_create(
        matter=matter,
        **candidate,
        defaults={"dismissed_by": person, "dismissed_at": timezone.now()},
    )


@transaction.atomic
def restore_related_suggestion(
    *,
    matter: Matter,
    actor: Any,
    candidate_matter: Matter | None = None,
    candidate_submission: Submission | None = None,
    candidate_archive_binary: OpinionArchiveBinary | None = None,
) -> bool:
    """«Taasta soovitus»: make a dismissed candidate eligible again."""
    _require_person(actor)
    candidate = _one_candidate(
        candidate_matter=candidate_matter,
        candidate_submission=candidate_submission,
        candidate_archive_binary=candidate_archive_binary,
    )
    deleted, _ = RelatedSuggestionDismissal.objects.filter(matter=matter, **candidate).delete()
    return deleted > 0
