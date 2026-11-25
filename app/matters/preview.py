"""«Eelvaade» — one Teema, read compactly beside the list it was opened from (UX-006).

The register row says what a file *is*; the Teema page says everything. The
preview is the step between: what the row cannot fit — the summary, the step and
who answers for it, the date that matters, where the procedure stands and the
last few things that happened — without leaving the list, its filters, its page
or its scroll position.

**A read, and only the reads the drawer prints.** It is not the Teema page's
`_overview_context` cut down: that builds forms, plans, recommendations and the
whole chronology, and a preview opened ten times while scanning a list must not
pay for any of it (target ≤ 15 queries, tests/test_matter_preview.py).

**Scoped exactly as everything else.** The Matter is read through
`Matter.objects.visible_to(user)` with the register row's own reader-scoped
prefetches, so a Matter the reader may not open is a 404 — the answer
`get_visible_matter` gives. The step comes from `open_action_prefetch`, the
milestone from `upcoming_milestone_prefetch` and the last acts from Minu asjad's
*Viimased tegevused* narrowed to this file (`recent_activity`), each of which
reads children through their own visibility: a restricted step, deadline or
note below a visible Matter is not named here either (docs/adr/0005, AUTH-003).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.contrib.postgres.expressions import ArraySubquery
from django.db.models import OuterRef, Subquery
from django.utils import timezone

from app.matters import legal_process
from app.matters.models import Matter, MatterStageEpisode
from app.matters.next_step import upcoming_milestone_prefetch
from app.matters.recent_activity import matter_recent_activity
from app.matters.selectors import open_action_prefetch
from app.matters.work_items import annotate_response_obligation
from app.taxonomy.models import LegalInstrumentType
from app.workflow.stage_flow import MONITORING_STOPPED

#: How many of the newest acts the drawer lists.
RECENT_ACTIVITY_LIMIT = 3


@dataclass(frozen=True)
class PreviewPhase:
    """One node of the procedure strip: its word and where the file stands to it."""

    label: str
    #: ``done``, ``current`` or ``ahead``.
    state: str
    conditional: bool = False


def procedure_strip(matter: Matter) -> tuple[str, list[PreviewPhase]]:
    """The file's pattern and its phases, marked done, current or ahead.

    `legal_process.phase_context` — the same resolution the Teema page's rail
    starts from — handed the row and its `Õigusakt` keys this request has just
    read, so it asks nothing again. Only the phases: dated steps and
    milestones are the rail's, and the drawer names the next milestone on its
    own line. A file whose `Hetkeseis` the pattern cannot place gets the words
    and no position, exactly as the rail reads it (docs/adr/0092 §13).
    """
    stage_key = matter.stage.key if matter.stage else None
    stage_label = matter.stage.label_et if matter.stage else None
    if stage_key == MONITORING_STOPPED:
        # «Rohkem ei tegele» is not a node: the rail stands on the period before
        # it (docs/adr/0131 §9). `phase_context` would read that period with a
        # query of its own; `preview_matter` already read it beside the row, so
        # the snapshot carries it and the answer is the same.
        stage_key = getattr(matter, "preview_earlier_stage_key", None) or ""
        stage_label = getattr(matter, "preview_earlier_stage_label", None) or ""
    context = legal_process.phase_context(
        matter=matter,
        instrument_keys=frozenset(getattr(matter, "preview_instrument_keys", None) or ()),
        snapshot=(stage_key, stage_label, matter.track, matter.disposition),
    )
    pattern = context.pattern
    if pattern is None:
        return "", []
    current = pattern.index_of_phase(context.current_phase) if context.current_phase else None
    phases = []
    for index, node in enumerate(pattern.nodes):
        if current is None:
            state = "ahead"
        elif index < current:
            state = "done"
        elif index == current:
            state = "current"
        else:
            state = "ahead"
        phases.append(PreviewPhase(node.label, state, node.conditional))
    return pattern.label, phases


def preview_matter(user: Any, pk: Any) -> Matter | None:
    """The Matter as this reader may see it, or ``None`` — the caller answers 404.

    `Matter.objects.visible_to(user)` with the register row's own reader-scoped
    prefetches (`open_action_prefetch`, `upcoming_milestone_prefetch`) and its
    owed-deadline annotation, so the drawer's step, milestone and dates are the
    row's by construction. The stage of the period before «Rohkem ei tegele» is
    read in the same statement (`procedure_strip`), so a stopped Teema costs no
    more than any other. Not `matter_list_queryset` whole: its last-activity
    annotations and two list prefetches buy nothing the drawer prints.
    """
    earlier = (
        MatterStageEpisode.objects.filter(matter_id=OuterRef("pk"))
        .exclude(stage__key=MONITORING_STOPPED)
        .order_by("-sequence")
    )
    return (
        annotate_response_obligation(
            Matter.objects.visible_to(user)
            .select_related("owner", "stage", "closed_by")
            .prefetch_related(open_action_prefetch(user), upcoming_milestone_prefetch(user))
            .annotate(
                preview_instrument_keys=ArraySubquery(
                    LegalInstrumentType.objects.filter(matters=OuterRef("pk")).values("key")
                ),
                preview_earlier_stage_key=Subquery(earlier.values("stage__key")[:1]),
                preview_earlier_stage_label=Subquery(earlier.values("stage__label_et")[:1]),
            ),
            user,
        )
        .filter(pk=pk)
        .first()
    )


def preview_context(matter: Matter, user: Any, *, today: date | None = None) -> dict[str, Any]:
    """Everything the drawer prints, read once."""
    today = today or timezone.localdate()
    open_actions = getattr(matter, "open_actions", [])
    pattern_label, phases = procedure_strip(matter)
    return {
        "matter": matter,
        "action": open_actions[0] if open_actions else None,
        "pattern_label": pattern_label,
        "phases": phases,
        "activities": matter_recent_activity(
            user, matter, limit=RECENT_ACTIVITY_LIMIT, today=today
        ),
        "today": today,
    }
