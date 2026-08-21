"""Reading and writing the operational photograph.

The capture is the easy half. The half worth reading carefully is
``visible_snapshots``: **a snapshot row never grants visibility.** Every read
joins the *live* Matter and authorizes there, so restricting a Matter today
removes it from last month's aggregate as well, for anybody who may not see it
now.

The alternative — trusting a visibility value stored at capture time — fails in
the direction that matters. A Matter restricted after its snapshot was taken
would keep appearing in historical charts for readers who lost access to it,
and nothing on screen would look wrong. This codebase already removed one
stored visibility column for that reason and is not adding another
(docs/adr/0005, Stage-2E brief 51).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.db.models import QuerySet
from django.utils import timezone

from app.core.authorization import apply as apply_scope
from app.core.authorization import matter_visibility_q, scope_for_user
from app.matters.enums import RecordMode
from app.matters.models import Matter
from app.reporting.models import OperationalMatterSnapshot
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction


def snapshot_population() -> QuerySet[Matter]:
    """What gets photographed: the open FULL portfolio, and nothing else.

    Unauthorized on purpose — the capture command runs as the system and must
    record the whole department, or the history would depend on who happened to
    run it. Authorization happens on the way *out*, in
    :func:`visible_snapshots`.
    """
    return (
        Matter.objects.filter(is_open=True, record_mode=RecordMode.FULL)
        .select_related("owner", "stage")
        .order_by("created_at")
    )


def visible_snapshots(viewer: Any) -> QuerySet[OperationalMatterSnapshot]:
    """Snapshot rows whose *live* Matter this viewer may read."""
    return apply_scope(
        OperationalMatterSnapshot.objects.select_related("matter", "owner"),
        matter_visibility_q(scope_for_user(viewer), prefix="matter__"),
    )


def capture(*, on: date | None = None) -> tuple[int, int]:
    """Photograph the operational portfolio for one day. Idempotent.

    Returns ``(created, updated)``. Re-running for the same date refreshes the
    rows rather than duplicating them — the unique constraint makes that a
    property of the schema rather than of this function's care.

    Only ever writes today's or an explicitly named date's picture from the
    *current* state. There is no reconstruction of an earlier day, because the
    data to do it honestly does not exist (brief 52).
    """
    snapshot_date = on or timezone.localdate()
    now = timezone.now()

    open_actions = {
        action.matter_id: action
        for action in NextAction.objects.filter(status=ActionStatus.OPEN).only(
            "matter_id", "kind", "date_semantics", "target_date"
        )
    }

    created = 0
    updated = 0
    for matter in snapshot_population().iterator(chunk_size=500):
        action = open_actions.get(matter.pk)
        stage = matter.stage
        defaults = {
            "owner": matter.owner,
            "stage": stage,
            "stage_key": stage.key if stage is not None else "",
            "stage_label": stage.label_et if stage is not None else "",
            "track": matter.track,
            "next_action_kind": action.kind if action is not None else "",
            "next_action_date_semantics": action.date_semantics if action is not None else "",
            "next_action_date": action.target_date if action is not None else None,
            "response_deadline": matter.response_deadline,
            "captured_at": now,
        }
        _, was_created = OperationalMatterSnapshot.objects.update_or_create(
            snapshot_date=snapshot_date, matter=matter, defaults=defaults
        )
        if was_created:
            created += 1
        else:
            updated += 1

    return created, updated
