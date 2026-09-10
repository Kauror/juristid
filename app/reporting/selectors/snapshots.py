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

The Matter is not the only thing a row projects, and that is what
``visible_snapshots`` had wrong. Three of its columns are copied from a
``NextAction``, which is a ``VisibilityInheritingModel``: a step may be
RESTRICTED below a Matter the whole department reads. Scoping the row by the
Matter alone therefore published a restricted step's kind, date meaning and
date to any reader of its Matter — a projection broader than its source, which
is the one thing docs/adr/0038 forbids. Closed here; the reasoning is in
``visible_snapshots`` and in docs/adr/0068.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.db.models import (
    BooleanField,
    Case,
    CharField,
    DateField,
    Exists,
    F,
    OuterRef,
    QuerySet,
    Value,
    When,
)
from django.utils import timezone

from app.core.authorization import Scope, child_visibility_q, matter_visibility_q, scope_for_user
from app.core.authorization import apply as apply_scope
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


def _next_action_is_visible(scope: Scope) -> Any:
    """Whether this row's photographed step is one ``scope`` may read.

    ``child_visibility_q`` unchanged, asked of the **live** ``NextAction`` the
    row points at — the same predicate, over the same table, that
    ``NextAction.objects.visible_to`` uses on every other surface. Restricting a
    step today therefore blanks it out of last month's photograph on the next
    query, with nothing stored being rewritten, exactly as restricting a Matter
    already did one level up.

    Asked as an ``Exists`` subquery rather than through the foreign key,
    deliberately. The participation half of the predicate reaches through
    ``collaborators``, and a second many-to-many join inside an annotation
    multiplies rows behind the ``DISTINCT`` that
    :func:`app.core.authorization.apply` already applied — the fan-out
    ``scoped_count`` exists to warn about. A subquery cannot fan out.

    A null pointer matches nothing and so reads False: a row captured before the
    pointer existed, or one whose step was deleted out from under it, blanks
    rather than publishes. Authorization whitelists.
    """
    if scope.sees_all_restricted:
        # Nothing to ask. This scope reads every child of every Matter it can
        # reach, so the subquery could only ever answer yes — while rows
        # captured before the pointer existed would answer no, hiding history
        # from the one reader entitled to all of it.
        return Value(True, output_field=BooleanField())
    if not scope.is_authenticated:
        return Value(False, output_field=BooleanField())
    return Exists(
        apply_scope(
            NextAction.objects.filter(pk=OuterRef("next_action")),
            child_visibility_q(scope),
        )
    )


def visible_snapshots(viewer: Any) -> QuerySet[OperationalMatterSnapshot]:
    """Rows whose *live* Matter this viewer may read, with the next-action
    columns blanked when the step behind them is not theirs to read.

    Two decisions, and the second is the finding this was rewritten for (F-4 of
    the 2026-09-09 restricted-data leakage audit).

    **Blank the columns, never drop the row.** The same audit found five
    populations that dropped a visible Matter the moment a restricted child
    appeared, and that *is* the disclosure: a reader who watches a named file
    leave a list learns restricted work happened on it. A row that stays with
    three empty columns says nothing, because it is the same shape as a day on
    which the Matter genuinely had no next action. Blanking is not a politeness
    here; it is the property that keeps this from being an existence oracle.

    **Decide it from the step's identity, not from a stored copy of its
    visibility.** Storing the override beside the copied facts would have been
    one column and no subquery, and it is the wrong answer: it goes stale in the
    fail-open direction. A lawyer who decides on Tuesday that Monday's step was
    sensitive restricts the live action — and every photograph taken before that
    moment would keep publishing its kind and its date, with nothing on screen
    looking wrong. That is this very defect, time-shifted, and it is the failure
    ADR 0005 removed a stored visibility column to be rid of. So the row records
    *which* step it photographed and the read derives the rest live, the way
    ``SearchDocument`` holds live foreign keys to the children it projects
    (docs/adr/0013, 0038).

    The price is a nullable column and a correlated subquery per read, plus one
    honest gap: rows captured before that column existed cannot say which step
    they photographed, so their next-action facts blank for every reader who
    does not already see every restricted child. No backfill can fix it —
    nothing recorded the answer — and this module refuses manufactured history
    on principle (Stage-2E brief 52).

    The scoped values arrive as ``visible_next_action_kind``,
    ``visible_next_action_date_semantics`` and ``visible_next_action_date``,
    beside a ``next_action_is_visible`` flag. They are annotations rather than
    the stored columns because Django will not let one shadow the other, and
    they are computed in **SQL** because the first surface to read this table
    will be a chart: blanking done in Python would be right in the template and
    silently wrong in the ``values().annotate()`` underneath it.
    ``OperationalMatterSnapshot.next_action_facts`` prefers them, so
    ``has_next_action`` and ``was_overdue`` on a row read through here are
    scoped too.

    **An aggregate over these rows must clear the ordering first.**
    ``OperationalMatterSnapshot.Meta.ordering`` names the Matter, which resolves
    through *its* ordering into three further columns, and all of them join the
    ``GROUP BY`` — so ``.values(...).annotate(...)`` without a preceding
    ``.order_by()`` returns one group per row rather than one per bucket. That
    is not new here and it is not this function's to fix, but it is the first
    thing the first chart will hit, and a count that is wrong per reader is the
    reconciliation defect this codebase forbids elsewhere.
    """
    scope = scope_for_user(viewer)
    rows = apply_scope(
        OperationalMatterSnapshot.objects.select_related("matter", "owner"),
        matter_visibility_q(scope, prefix="matter__"),
    )
    return rows.annotate(next_action_is_visible=_next_action_is_visible(scope)).annotate(
        visible_next_action_kind=Case(
            When(next_action_is_visible=True, then=F("next_action_kind")),
            default=Value(""),
            output_field=CharField(max_length=16),
        ),
        visible_next_action_date_semantics=Case(
            When(next_action_is_visible=True, then=F("next_action_date_semantics")),
            default=Value(""),
            output_field=CharField(max_length=32),
        ),
        visible_next_action_date=Case(
            When(next_action_is_visible=True, then=F("next_action_date")),
            default=Value(None, output_field=DateField()),
            output_field=DateField(),
        ),
    )


def capture(*, on: date | None = None) -> tuple[int, int]:
    """Photograph the operational portfolio for one day. Idempotent.

    Returns ``(created, updated)``. Re-running for the same date refreshes the
    rows rather than duplicating them — the unique constraint makes that a
    property of the schema rather than of this function's care.

    Only ever writes today's or an explicitly named date's picture from the
    *current* state. There is no reconstruction of an earlier day, because the
    data to do it honestly does not exist (brief 52).

    Deliberately unauthorized, and it stays that way: it records the whole
    department, restricted steps included, and :func:`visible_snapshots` decides
    who may read what. What it now also records is *which* step each row copied,
    so that decision has something to derive from.
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
            "next_action": action,
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
