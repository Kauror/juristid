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

from datetime import date, datetime
from typing import Any

from django.db import connection, transaction
from django.db.models import (
    BooleanField,
    Case,
    CharField,
    DateField,
    Exists,
    F,
    OuterRef,
    QuerySet,
    UUIDField,
    Value,
    When,
)
from django.utils import timezone

from app.accounts.enums import UserRole
from app.core.authorization import Scope, child_scope_q, matter_visibility_q, scope_for_user
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
            # `NextAction`'s own rule: a follow-up check is never more visible
            # than its opinion (docs/adr/0146 §10).
            child_scope_q(NextAction, scope),
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
        visible_next_action_date_precision=Case(
            When(next_action_is_visible=True, then=F("next_action_date_precision")),
            default=Value(""),
            output_field=CharField(max_length=16),
        ),
        visible_next_action_responsible_id=Case(
            When(next_action_is_visible=True, then=F("next_action_responsible")),
            default=Value(None, output_field=UUIDField()),
            output_field=UUIDField(),
        ),
        **_work_state_annotations(scope),
    )


def _work_state_annotations(scope: Scope) -> dict[str, Any]:
    """«Tähtaja ületanud» and «Vajab sekkumist» memberships, as this scope may read them.

    The two flags were decided at capture time over the **whole** department,
    restricted children included (:func:`work_state_memberships`). Unlike the
    step columns they do not name the child that decided them — a Teema can be
    overdue because of a restricted step, a restricted important date or a
    restricted round — so no per-row probe can tell whether this reader may see
    the reason. The rule is therefore the conservative one: a scope that sees
    every restricted child reads the flags; any other scope reads NULL, which
    every consumer treats as «ei mõõdetud», never as False. Since docs/adr/0181
    every business reader's scope sees every restricted child, so in practice
    the department reads the flags and the rule costs nothing; it is stated (and
    tested) so that a narrower scope can never get a count derived from a child
    it may not see.
    """
    if scope.sees_all_restricted:
        return {
            "visible_is_overdue": F("is_overdue"),
            "visible_needs_attention": F("needs_attention"),
        }
    unknown = Value(None, output_field=BooleanField())
    return {"visible_is_overdue": unknown, "visible_needs_attention": unknown}


class _WholeDepartment:
    """The reader the capture evaluates work-state membership as.

    The capture is unauthorized by design (:func:`snapshot_population`): the
    photograph records the department, and :func:`visible_snapshots` decides
    who may read it. The canonical work-state selectors
    (`app.matters.work_items`) take a reader, so the capture hands them this
    one — a business-read role, so `scope_for_user` gives the scope that sees
    every Matter and every restricted child, which is exactly the whole
    department. It is not a `User`: no primary key, never an actor, never
    written anywhere.
    """

    is_authenticated = True
    is_active = True
    is_anonymous = False
    is_staff = False
    is_superuser = False
    pk = None
    id = None
    role = UserRole.DEPARTMENT_HEAD.value

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<WholeDepartment>"


WHOLE_DEPARTMENT = _WholeDepartment()


def work_state_memberships(on: date) -> tuple[set[Any], set[Any]]:
    """Which Teemad were «Tähtaja ületanud» and «Vajab sekkumist» on ``on``.

    The live canonical populations, unchanged and over the whole department:
    «Tähtaja ületanud» is `work_items.WORK_OVERDUE` (the Matters of
    `overdue_items` — a DO + DEADLINE step late by `app.workflow.lateness`,
    planned follow-up checks included; an outstanding response deadline past;
    an important date past; a feedback wait past). «Vajab sekkumist» is
    `WORK_NEEDS_ATTENTION` (overdue ∪ ripe for review ∪ no next action ∪ no
    owner). One read of the work model serves both.
    """
    from app.matters import work_items as wi

    items = wi.work_items(WHOLE_DEPARTMENT, today=on)
    overdue = wi.work_population_ids(WHOLE_DEPARTMENT, wi.WORK_OVERDUE, today=on, items=items)
    attention = wi.work_population_ids(
        WHOLE_DEPARTMENT, wi.WORK_NEEDS_ATTENTION, today=on, items=items
    )
    return overdue, attention


def capture(*, on: date | None = None, now: datetime | None = None) -> tuple[int, int]:
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
    so that decision has something to derive from — and, since the amendment of
    2026-10-30 (docs/adr/0159, D4), the step's precision and responsible person
    and the Teema's two work-state memberships (:func:`work_state_memberships`).
    """
    snapshot_date = on or timezone.localdate()
    now = now or timezone.now()

    open_actions = {
        action.matter_id: action
        for action in NextAction.objects.filter(status=ActionStatus.OPEN).only(
            "matter_id", "kind", "date_semantics", "target_date", "date_precision", "responsible"
        )
    }
    overdue, attention = work_state_memberships(snapshot_date)

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
            "next_action_date_precision": action.date_precision if action is not None else "",
            "next_action_responsible_id": action.responsible_id if action is not None else None,
            "response_deadline": matter.response_deadline,
            "is_overdue": matter.pk in overdue,
            "needs_attention": matter.pk in attention,
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


#: The local hour from which the day's photograph may be taken automatically —
#: early, before the working day changes anything (the schedule the capture
#: command's docstring has always asked for).
DAILY_CAPTURE_HOUR = 3

#: Advisory lock for the automatic daily capture. Distinct from every key in
#: use: search 24601, organisations 24602, titles 24603, storage quota 24604,
#: reminders 24605, taxonomy vocabulary 24606, Lovable sync 24607, deadline notifications 24609.
DAILY_CAPTURE_LOCK = (24608, 1)


def capture_daily_if_missing(now: datetime | None = None) -> bool:
    """Take today's photograph once, if it is late enough and none exists yet.

    Meant for a loop worker that calls it every few minutes. Returns whether it
    captured. Never a second capture on the same local day: :func:`capture`
    *refreshes* an existing day's rows, so an automatic re-run at noon would
    overwrite the early-morning picture with the afternoon's, and a trend would
    compare two different times of day. A deliberate same-day refresh stays the
    management command's, run by a person.

    Europe/Tallinn local time decides both the hour and the day. Concurrency-
    safe: the existence check and the capture run under one transaction-scoped
    advisory lock, so two workers that wake together write one photograph.

    **Known limitation, accepted:** "already captured today" is read from the
    rows themselves, and a day with no open FULL Teema writes none. Such a day
    is therefore photographed again on every call (harmless — there is nothing
    to overwrite) and has no rows for a comparison to find, so a boundary that
    falls on it borrows the nearest other photograph. A department with no open
    Teema at all is not a production state; recording empty days would need a
    capture-log table, which is not worth one (docs/adr/0159 §7).
    """
    now = now or timezone.now()
    local = timezone.localtime(now)
    if local.hour < DAILY_CAPTURE_HOUR:
        return False
    day = local.date()
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", list(DAILY_CAPTURE_LOCK))
        if OperationalMatterSnapshot.objects.filter(snapshot_date=day).exists():
            return False
        capture(on=day, now=now)
    return True
