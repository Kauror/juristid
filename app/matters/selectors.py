"""Read models for the work surfaces.

Every function here scopes by authorization **before** filtering, ordering,
counting or slicing. A restricted Matter therefore cannot influence a count, a
page boundary or an attention flag, which is the failure mode a UI-level hide
would not catch (master specification 5.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.db.models import Exists, OuterRef, Prefetch, Q, QuerySet
from django.utils import timezone

from app.matters.enums import REGISTER_YEAR_ORIGINS, RecordMode
from app.matters.models import Matter
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics
from app.workflow.models import NextAction

HORIZON_DAYS = 7

#: The URL value that means "no usable reporting year". A word rather than a
#: blank, so that `?aasta=` (a cleared filter) and `?aasta=teadmata` (a
#: deliberate ask for the unknown bucket) cannot be confused.
UNKNOWN_YEAR = "teadmata"


def register_year_q(*, start: int, end: int) -> Q:
    """Matters whose reporting year is a *register* year inside the span.

    Both the year chart and the register's own `?aasta=` filter build their
    query here, which is the only reason the bar and the list it opens can be
    asserted to agree. Two similar conditions written in two places is how a
    count and its drill-through start disagreeing (Stage-2E brief 66).
    """
    return Q(
        reporting_year__gte=start,
        reporting_year__lte=end,
        origin__in=REGISTER_YEAR_ORIGINS,
    )


def unknown_register_year_q() -> Q:
    """Matters with no reporting year, or one that is not a register year.

    The exact complement of :func:`register_year_q` over all years, so the two
    buckets partition the population and nothing falls between them.
    """
    return Q(reporting_year__isnull=True) | ~Q(origin__in=REGISTER_YEAR_ORIGINS)


def open_action_prefetch() -> Prefetch:
    """Attach the one open action without a query per row."""
    return Prefetch(
        "next_actions",
        queryset=NextAction.objects.filter(status=ActionStatus.OPEN).select_related("responsible"),
        to_attr="open_actions",
    )


def matter_list_queryset(user: Any) -> QuerySet[Matter]:
    """The base register query: authorized, with everything a row displays."""
    return (
        Matter.objects.visible_to(user)
        .select_related("owner", "stage", "source_organisation", "addressee_organisation")
        .prefetch_related(open_action_prefetch(), "policy_areas")
    )


def current_action_of(matter: Matter) -> NextAction | None:
    """Read the prefetched open action, falling back to a query if absent."""
    prefetched = getattr(matter, "open_actions", None)
    if prefetched is not None:
        return prefetched[0] if prefetched else None
    return matter.next_actions.filter(status=ActionStatus.OPEN).first()


def visible_actions(user: Any) -> QuerySet[NextAction]:
    return (
        NextAction.objects.visible_to(user)
        .filter(status=ActionStatus.OPEN)
        .select_related(
            "matter",
            "matter__stage",
            "matter__owner",
            "responsible",
        )
    )


@dataclass(frozen=True)
class WorkGroup:
    """One band of the Teen list. Bands are ordered by urgency, not by date."""

    key: str
    label: str
    actions: list[NextAction]

    @property
    def count(self) -> int:
        return len(self.actions)


def my_do_groups(user: Any, today: date | None = None) -> list[WorkGroup]:
    """DO actions for one person, banded by real urgency.

    Only DO + DEADLINE can be late, so only those appear as overdue. A DO
    without a deadline is real work with no date attached and belongs at the
    end rather than in a false urgency band.
    """
    today = today or timezone.localdate()
    horizon = today + timedelta(days=HORIZON_DAYS)

    mine = visible_actions(user).filter(responsible=user, kind=ActionKind.DO)

    deadline = Q(date_semantics=DateSemantics.DEADLINE)
    overdue = list(mine.filter(deadline, target_date__lt=today).order_by("target_date"))
    today_actions = list(mine.filter(deadline, target_date=today).order_by("matter__title"))
    soon = list(
        mine.filter(deadline, target_date__gt=today, target_date__lte=horizon).order_by(
            "target_date"
        )
    )
    later = list(
        mine.filter(Q(target_date__gt=horizon) | Q(target_date__isnull=True)).order_by(
            "target_date"
        )
    )

    return [
        WorkGroup("overdue", "Tähtaeg möödas", overdue),
        WorkGroup("today", "Täna", today_actions),
        WorkGroup("soon", f"Järgmise {HORIZON_DAYS} päeva jooksul", soon),
        WorkGroup("later", "Hiljem või tähtajata", later),
    ]


def my_waiting_actions(user: Any, today: date | None = None) -> list[NextAction]:
    """WAIT and MONITOR actions, review-due first.

    These are never described as overdue. Waiting for a ministry is the normal
    state of a great deal of this work, and labelling it as a missed task would
    make the whole list untrustworthy (master specification 18.8).
    """
    today = today or timezone.localdate()
    actions = list(
        visible_actions(user)
        .filter(responsible=user, kind__in=(ActionKind.WAIT, ActionKind.MONITOR))
        .order_by("target_date", "matter__title")
    )
    return sorted(
        actions,
        key=lambda action: (
            not action.is_due_for_review(today),
            action.target_date or date.max,
        ),
    )


def matters_without_next_action(user: Any) -> QuerySet[Matter]:
    """Open FULL Matters carrying no current instruction.

    This is the one attention state that cannot be derived from a date, which is
    exactly why it needs a query of its own: without it a Matter simply stops
    appearing anywhere and goes quiet (design handoff, recommendation 1).
    """
    has_open_action = NextAction.objects.filter(matter=OuterRef("pk"), status=ActionStatus.OPEN)
    return (
        matter_list_queryset(user)
        .filter(is_open=True, record_mode=RecordMode.FULL)
        .annotate(has_action=Exists(has_open_action))
        .filter(has_action=False)
    )


@dataclass(frozen=True)
class AttentionItem:
    """A deterministic, actionable data-quality problem.

    Nothing speculative appears here. A warning a lawyer cannot act on, or
    disagrees with, teaches them to ignore the panel.
    """

    key: str
    label: str
    matter: Matter
    detail: str = ""


def my_attention_items(user: Any, today: date | None = None) -> list[AttentionItem]:
    today = today or timezone.localdate()
    items: list[AttentionItem] = []

    owned = (
        Matter.objects.visible_to(user)
        .filter(owner=user, is_open=True, record_mode="FULL")
        .select_related("stage")
    )

    for matter in matters_without_next_action(user).filter(owner=user)[:50]:
        items.append(
            AttentionItem(
                key="no_next_action",
                label="Järgmiseks puudub",
                matter=matter,
                detail="Aktiivsel teemal ei ole määratud järgmist tegevust.",
            )
        )

    # A response deadline that has passed with nothing sent. Only flagged where
    # the question is meaningful: the Matter is still open and a deadline was
    # actually recorded.
    sent_submission = Submission.objects.filter(matter=OuterRef("pk"), status=SubmissionStatus.SENT)
    overdue_response = (
        owned.filter(response_deadline__lt=today)
        .annotate(has_sent=Exists(sent_submission))
        .filter(has_sent=False)
    )
    for matter in overdue_response[:50]:
        items.append(
            AttentionItem(
                key="deadline_without_submission",
                label="Tähtaeg möödas, arvamust ei ole saadetud",
                matter=matter,
                detail=f"Arvamuse tähtaeg oli {matter.response_deadline:%d.%m.%Y}.",
            )
        )

    return items


def my_active_matters(user: Any) -> QuerySet[Matter]:
    """The signed-in person's open portfolio.

    Called inventory, never workload: a count of open files says nothing about
    effort, and the specification forbids presenting it as if it did
    (master specification 7.2, 18.8).
    """
    return (
        matter_list_queryset(user)
        .filter(Q(owner=user) | Q(collaborators=user), is_open=True)
        .distinct()
        .order_by("-updated_at")
    )
