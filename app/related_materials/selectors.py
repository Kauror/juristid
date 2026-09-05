"""What the Matter page shows immediately: the decisions already made.

Confirmed relations and chosen background material are canonical rows and
cheap to read, so they render with the page. Suggestions are not read here at
all — they cost queries a lawyer who never opens «Võimalikud seosed» should not
pay, and the fragment view computes them on request (docs/adr/0061 §7).

Both readers apply the viewer's own visibility to the far side of every row.
A relation to a Matter this reader may not open is not shown, not counted and
not hinted at; a background Submission on a restricted Matter is the same; an
archive letter is offered only to a reader of the archive. A relation is a fact
about two files, never a key to one of them (§5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.db.models import Q
from django.urls import reverse

from app.legacy_import.opinion_access import may_read_archive
from app.matters.models import Matter
from app.related_materials.models import MatterBackgroundMaterial, MatterRelation
from app.submissions.models import Submission


@dataclass(frozen=True)
class ConfirmedRelation:
    """One confirmed related Matter, seen from this Matter's side."""

    relation: MatterRelation
    other: Matter

    @property
    def state_label(self) -> str:
        return "avatud" if self.other.is_open else "suletud"


@dataclass(frozen=True)
class BackgroundItem:
    """One chosen piece of background material, ready to render."""

    row: MatterBackgroundMaterial
    kind: str
    label: str
    key: Any
    title: str
    date: date | None
    source_reference: str
    source_title: str
    recipient: str
    open_url: str

    @property
    def form_kind(self) -> str:
        return "arvamus" if self.kind == "SUBMISSION" else "arhiiv"


def confirmed_relations(matter: Matter, viewer: Any) -> list[ConfirmedRelation]:
    """The Matters this one is confirmed related to, that this reader may open."""
    visible = Matter.objects.visible_to(viewer).values("pk")
    rows = MatterRelation.objects.filter(
        Q(matter_a=matter, matter_b__in=visible) | Q(matter_b=matter, matter_a__in=visible)
    ).select_related(
        "matter_a",
        "matter_a__addressee_organisation",
        "matter_b",
        "matter_b__addressee_organisation",
    )
    found = [
        ConfirmedRelation(
            relation=row, other=row.matter_b if row.matter_a_id == matter.pk else row.matter_a
        )
        for row in rows
    ]
    found.sort(
        key=lambda item: (
            -(item.other.reference_year or 0),
            -(item.other.reference_number or 0),
            item.other.title.casefold(),
            str(item.other.pk),
        )
    )
    return found


def background_materials(matter: Matter, viewer: Any) -> list[BackgroundItem]:
    """The background chosen for this Matter, that this reader may open."""
    condition = Q(submission__in=Submission.objects.visible_to(viewer).values("pk"))
    if may_read_archive(viewer):
        condition |= Q(archive_binary__isnull=False)
    rows = (
        MatterBackgroundMaterial.objects.filter(matter=matter)
        .filter(condition)
        .select_related(
            "submission",
            "submission__matter",
            "archive_binary",
            "archive_binary__search_document",
        )
        .prefetch_related("submission__recipients")
    )
    found = [_background_item(row) for row in rows]
    found.sort(
        key=lambda item: (
            -(item.date.toordinal() if item.date else 0),
            item.title.casefold(),
            str(item.key),
        )
    )
    return found


def _background_item(row: MatterBackgroundMaterial) -> BackgroundItem:
    if row.submission is not None:
        submission = row.submission
        source = submission.matter
        return BackgroundItem(
            row=row,
            kind="SUBMISSION",
            label="Arvamus",
            key=submission.pk,
            title=submission.title,
            date=submission.sent_at.date() if submission.sent_at else None,
            source_reference=source.display_reference,
            source_title=source.title,
            recipient=", ".join(
                sorted(organisation.name for organisation in submission.recipients.all())
            ),
            open_url=reverse("matters:matter_position", kwargs={"pk": source.pk}),
        )
    binary = row.archive_binary
    if binary is None:  # pragma: no cover - the database constraint forbids it
        raise ValueError("A background row must name a Submission or an archive binary.")
    projection = getattr(binary, "search_document", None)
    return BackgroundItem(
        row=row,
        kind="ARCHIVE",
        label="Arhiivikiri",
        key=binary.pk,
        title=(projection.title if projection is not None else "") or "Pealkirjata kiri",
        date=projection.document_date if projection is not None else None,
        source_reference=(
            str(projection.source_year) if projection is not None and projection.source_year else ""
        ),
        source_title="",
        recipient=projection.recipient if projection is not None else "",
        open_url=reverse("legacy_import:opinion_archive_detail", kwargs={"pk": binary.pk}),
    )


@dataclass(frozen=True)
class RelatedMaterials:
    """What the ordinary Matter page renders: decisions, not suggestions."""

    relations: tuple[ConfirmedRelation, ...]
    background: tuple[BackgroundItem, ...]

    @property
    def is_empty(self) -> bool:
        return not self.relations and not self.background


def related_materials_for(matter: Matter, viewer: Any) -> RelatedMaterials:
    """Two queries, and nothing about what might be related."""
    return RelatedMaterials(
        relations=tuple(confirmed_relations(matter, viewer)),
        background=tuple(background_materials(matter, viewer)),
    )
