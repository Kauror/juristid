"""What the Matter page shows immediately: the decisions already made.

Confirmed relations and chosen background material are canonical rows and
cheap to read, so they render with the page. Suggestions are not read here at
all — they cost queries a lawyer who never opens «Võimalikud seosed» should not
pay, and the fragment view computes them on request (docs/adr/0062 §7).

Both readers apply the viewer's own visibility to the far side of every row.
A relation to a Matter this reader may not open is not shown, not counted and
not hinted at; a background Submission on a restricted Matter is the same; an
archive letter is offered only to a reader of the archive. A relation is a fact
about two files, never a key to one of them (§5).

The earlier stages of the Teema's continuation history are the third thing, and
the only one nobody chose here: they follow from `Matter.superseded_by`, walked
through every branch by `historical_predecessors` in this reader's scope
(app/matters/continuations.py, docs/adr/0209). They are shown, never edited. A
predecessor that somebody also related by hand is one row, not two — the stage
row carrying the manual relation and its remove control (§2 of 0209).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from app.legacy_import.opinion_access import may_read_archive
from app.matters.continuations import EarlierStage, historical_predecessors
from app.matters.models import Matter
from app.related_materials.models import MatterBackgroundMaterial, MatterRelation
from app.submissions.models import Submission
from app.submissions.opinions import opinions_url


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
            # The Tallinn day, not the UTC one PostgreSQL returns: an
            # opinion sent at 01:30 Tallinn is still the 12th in UTC
            # (config/settings.py `TIME_ZONE`).
            date=timezone.localdate(submission.sent_at) if submission.sent_at else None,
            source_reference=source.display_reference,
            source_title=source.title,
            recipient=", ".join(
                sorted(organisation.name for organisation in submission.recipients.all())
            ),
            # Dokumendid filtered to opinions, not the retired per-Matter
            # Arvamused page, which is only a redirect now (docs/adr/0061).
            open_url=opinions_url(source),
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
class EarlierStageItem:
    """One readable earlier stage, and the manual relation to it if there is one.

    `relation` is the confirmed `MatterRelation` to the same Matter, when
    somebody also linked it by hand. The row then says both bases and keeps the
    manual relation's `Eemalda seos`; the continuation itself has no control
    anywhere in this section (docs/adr/0209 §2).
    """

    stage: EarlierStage
    relation: ConfirmedRelation | None = None

    @property
    def matter(self) -> Matter:
        return self.stage.matter

    @property
    def depth(self) -> int:
        return self.stage.depth

    @property
    def state_label(self) -> str:
        return "avatud" if self.stage.matter.is_open else "suletud"

    @property
    def is_also_manual(self) -> bool:
        return self.relation is not None


@dataclass(frozen=True)
class RelatedMaterials:
    """What the ordinary Matter page renders: decisions and history, not suggestions."""

    #: Every confirmed relation this reader may open, the ones to an earlier
    #: stage included. Rendering reads `manual_rows`, which leaves those out
    #: because their stage row already carries them.
    relations: tuple[ConfirmedRelation, ...]
    background: tuple[BackgroundItem, ...]
    #: The readable earlier stages, earliest first (`historical_predecessors`).
    stages: tuple[EarlierStageItem, ...] = ()
    #: The walk met a stage it had already shown, or was cut at its bound. One
    #: honest note each; nothing beyond is named or counted.
    stages_cyclic: bool = False
    stages_truncated: bool = False

    @property
    def manual_rows(self) -> tuple[ConfirmedRelation, ...]:
        """The confirmed relations that are not already a row under `Varasemad etapid`."""
        on_stage_rows = {item.matter.pk for item in self.stages}
        return tuple(item for item in self.relations if item.other.pk not in on_stage_rows)

    @property
    def is_empty(self) -> bool:
        return not self.relations and not self.background and not self.stages

    @property
    def count(self) -> int:
        """Distinct visible rows: the count beside `Seotud materjalid`.

        A predecessor that is also related by hand is one row and counts once.
        """
        return len(self.manual_rows) + len(self.background) + len(self.stages)


def related_materials_for(matter: Matter, viewer: Any) -> RelatedMaterials:
    """Two queries and the history walk (one per level), nothing about what might be related."""
    relations = tuple(confirmed_relations(matter, viewer))
    by_matter = {item.other.pk: item for item in relations}
    history = historical_predecessors(matter, viewer)
    return RelatedMaterials(
        relations=relations,
        background=tuple(background_materials(matter, viewer)),
        stages=tuple(
            EarlierStageItem(stage=stage, relation=by_matter.get(stage.matter.pk))
            for stage in history.stages
        ),
        stages_cyclic=history.cyclic,
        stages_truncated=history.truncated,
    )
