"""Ülevaade — what is happening across the visible portfolio.

Distinct from Minu töö, which answers "what do *I* have to do". This answers
"what is the state of the department's files", and the difference matters: a
department head opening the same page as a lawyer should see the same shape of
truth, scoped to what each may read.

Two rules run through every function here.

**Authorization before arithmetic.** Every queryset starts from
``Matter.objects.visible_to(user)`` and is filtered, grouped, counted and sliced
only after that. A restricted Matter the reader may not see contributes nothing
to a total, a bar, an owner tally or an attention row. Hiding it at render time
would leave it inside the counts, which is the disclosure that is easy to miss
because nothing on screen looks wrong.

**Nothing here is a performance metric.** Counts by owner are *inventory* — how
many open files sit with someone — and are never labelled workload, throughput
or productivity. The specification forbids presenting a count of open matters as
if it measured effort (18.8), and a dashboard that ranks colleagues is a
dashboard people learn to game.

Nothing is invented either: no severity score, no predicted risk, no
"most productive", no response rate. Every number is a count of rows that exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from django.db.models import Count, Exists, F, OuterRef, QuerySet
from django.urls import reverse
from django.utils import timezone

from app.matters.enums import RecordMode
from app.matters.models import Matter
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics
from app.workflow.models import NextAction

#: The summary cards look this far ahead.
DEADLINE_HORIZON_DAYS = 7

#: The upcoming table looks further, because a fortnight is the planning unit a
#: department review actually uses.
UPCOMING_HORIZON_DAYS = 14

#: Caps. A dashboard that renders four hundred rows is a dashboard nobody reads,
#: and the counts above each list are the honest total either way.
ATTENTION_LIMIT = 40
UPCOMING_LIMIT = 40
RECENT_INCOMING_LIMIT = 10


def _teemad(**params: Any) -> str:
    """A link into the register with filters already applied.

    Every number on this page is a promise that a list exists behind it. Reusing
    the register's own query parameters keeps that promise honest: the count and
    the list come from the same filters rather than two similar definitions.
    """
    query = "&".join(f"{key}={value}" for key, value in params.items() if value)
    return f"{reverse('matters:matter_list')}?{query}" if query else reverse("matters:matter_list")


def active_matters(user: Any) -> QuerySet[Matter]:
    """Open FULL Matters the reader may see.

    ARCHIVE rows are excluded throughout the dashboard. They are historical
    evidence rather than live work, and counting a decade of imported register
    rows as "active" would make every number meaningless.
    """
    return Matter.objects.visible_to(user).filter(is_open=True, record_mode=RecordMode.FULL)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SummaryCard:
    key: str
    label: str
    count: int
    url: str
    note: str = ""


def overdue_actions(user: Any, today: date) -> QuerySet[NextAction]:
    """Genuinely late work, and nothing else.

    Public rather than private because the department-head dashboard counts the
    same thing, and a second definition of "overdue" written next door is how
    two pages start disagreeing about the same Matter (Stage-2F brief 32).

    Only DO + DEADLINE can be overdue. A WAIT whose review date has passed is
    due for a look, not missed — describing an ordinary dependency on a ministry
    as a failure is what makes a work queue stop being believed
    (master specification 18.8).
    """
    return (
        NextAction.objects.visible_to(user)
        .filter(
            status=ActionStatus.OPEN,
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date__lt=today,
        )
        .select_related("matter", "matter__owner", "matter__stage", "responsible")
    )


def reviews_due(user: Any, today: date) -> QuerySet[NextAction]:
    """WAIT and MONITOR whose review date has arrived. Never called overdue."""
    return (
        NextAction.objects.visible_to(user)
        .filter(
            status=ActionStatus.OPEN,
            kind__in=(ActionKind.WAIT, ActionKind.MONITOR),
            target_date__isnull=False,
            target_date__lte=today,
        )
        .select_related("matter", "matter__owner", "matter__stage", "responsible")
    )


def without_next_action(user: Any) -> QuerySet[Matter]:
    has_open = NextAction.objects.filter(matter=OuterRef("pk"), status=ActionStatus.OPEN)
    return active_matters(user).annotate(has_action=Exists(has_open)).filter(has_action=False)


def summary_cards(user: Any, today: date | None = None) -> list[SummaryCard]:
    today = today or timezone.localdate()
    horizon = today + timedelta(days=DEADLINE_HORIZON_DAYS)
    active = active_matters(user)

    return [
        SummaryCard(
            key="active",
            label="Aktiivsed teemad",
            count=active.count(),
            url=_teemad(olek="avatud", liik=RecordMode.FULL),
        ),
        SummaryCard(
            key="deadlines",
            label=f"Tähtajad {DEADLINE_HORIZON_DAYS} päeva jooksul",
            count=active.filter(
                response_deadline__gte=today, response_deadline__lte=horizon
            ).count(),
            url=_teemad(olek="avatud", liik=RecordMode.FULL),
            note="Arvamuse tähtaeg",
        ),
        SummaryCard(
            key="overdue",
            label="Tähtaeg möödas",
            count=overdue_actions(user, today).count(),
            url=reverse("matters:my_work"),
            note="Ainult tähtajaga tegevused",
        ),
        SummaryCard(
            key="no_action",
            label="Järgmine tegevus puudub",
            count=without_next_action(user).count(),
            url=_teemad(olek="avatud", liik=RecordMode.FULL),
        ),
        SummaryCard(
            key="unassigned",
            label="Vastutajata",
            count=active.filter(owner__isnull=True).count(),
            url=reverse("matters:inbox"),
        ),
    ]


# ---------------------------------------------------------------------------
# Attention
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttentionRow:
    """One thing somebody can actually do something about.

    ``order`` is a fixed rank per reason, not a computed severity. An invented
    score would be a number nobody can check and everybody would argue with.
    """

    order: int
    reason: str
    matter: Matter
    owner_name: str
    when: date | None
    date_label: str
    url: str


_UNASSIGNED = "Vastutaja puudub"
_NO_ACTION = "Järgmine tegevus puudub"
_OVERDUE = "Tegevuse tähtaeg möödas"
_NO_SUBMISSION = "Arvamuse tähtaeg möödas, arvamust ei ole saadetud"
_REVIEW_DUE = "Ülevaatuse aeg on käes"


def _stage_label(matter: Matter) -> str:
    stage = matter.stage
    return stage.label_et if stage is not None else "—"


def _owner_name(matter: Matter) -> str:
    owner = matter.owner
    return owner.display_name if owner is not None else "Vastutajata"


def attention_rows(user: Any, today: date | None = None) -> list[AttentionRow]:
    """Actionable states only, ordered by how late each one actually is.

    Reuses the same definitions the rest of the product uses rather than
    restating them: a second definition of "overdue" that drifts from the first
    is how two screens start disagreeing about the same Matter.
    """
    today = today or timezone.localdate()
    rows: list[AttentionRow] = []

    for action in overdue_actions(user, today).order_by("target_date")[:ATTENTION_LIMIT]:
        rows.append(
            AttentionRow(
                order=1,
                reason=_OVERDUE,
                matter=action.matter,
                owner_name=_owner_name(action.matter),
                when=action.target_date,
                date_label="Tähtaeg",
                url=reverse("matters:matter_detail", kwargs={"pk": action.matter_id}),
            )
        )

    sent = Submission.objects.filter(matter=OuterRef("pk"), status=SubmissionStatus.SENT)
    missed = (
        active_matters(user)
        .filter(response_deadline__lt=today)
        .annotate(has_sent=Exists(sent))
        .filter(has_sent=False)
        .select_related("owner", "stage")
        .order_by("response_deadline")
    )
    for missed_matter in missed[:ATTENTION_LIMIT]:
        rows.append(
            AttentionRow(
                order=2,
                reason=_NO_SUBMISSION,
                matter=missed_matter,
                owner_name=_owner_name(missed_matter),
                when=missed_matter.response_deadline,
                date_label="Arvamuse tähtaeg",
                url=reverse("matters:matter_detail", kwargs={"pk": missed_matter.pk}),
            )
        )

    for action in reviews_due(user, today).order_by("target_date")[:ATTENTION_LIMIT]:
        rows.append(
            AttentionRow(
                order=3,
                reason=_REVIEW_DUE,
                matter=action.matter,
                owner_name=_owner_name(action.matter),
                when=action.target_date,
                date_label=action.date_label or "Ülevaatus",
                url=reverse("matters:matter_detail", kwargs={"pk": action.matter_id}),
            )
        )

    unassigned = active_matters(user).filter(owner__isnull=True).select_related("stage")
    for matter in unassigned.order_by("-created_at")[:ATTENTION_LIMIT]:
        rows.append(
            AttentionRow(
                order=4,
                reason=_UNASSIGNED,
                matter=matter,
                owner_name=_owner_name(matter),
                when=matter.received_date,
                date_label="Saabus",
                url=reverse("matters:matter_detail", kwargs={"pk": matter.pk}),
            )
        )

    without_action = without_next_action(user).select_related("owner", "stage")
    for quiet in without_action.order_by("-updated_at")[:ATTENTION_LIMIT]:
        rows.append(
            AttentionRow(
                order=5,
                reason=_NO_ACTION,
                matter=quiet,
                owner_name=_owner_name(quiet),
                when=quiet.response_deadline,
                date_label="Arvamuse tähtaeg" if quiet.response_deadline else "",
                url=reverse("matters:matter_detail", kwargs={"pk": quiet.pk}),
            )
        )

    # Reason rank first, then genuinely oldest first inside a reason. Rows with
    # no date sort last rather than pretending to be urgent.
    return sorted(rows, key=lambda row: (row.order, row.when or date.max))[:ATTENTION_LIMIT]


# ---------------------------------------------------------------------------
# Upcoming
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UpcomingRow:
    when: date
    matter: Matter
    owner_name: str
    stage_label: str
    meaning: str
    url: str


#: What a date on this page actually means. Four different things share a
#: column, and collapsing them into one word called "deadline" is precisely the
#: conflation the register suffered from and this product exists to undo.
MEANING_RESPONSE = "Arvamuse tähtaeg"
MEANING_ACTION = "Tegevuse tähtaeg"
MEANING_REVIEW = "Ülevaatuse kuupäev"
MEANING_EXPECTED = "Oodatav aeg"

_SEMANTICS_MEANING = {
    DateSemantics.DEADLINE.value: MEANING_ACTION,
    DateSemantics.REVIEW_ON.value: MEANING_REVIEW,
    DateSemantics.EXPECTED_AROUND.value: MEANING_EXPECTED,
}


def upcoming_rows(user: Any, today: date | None = None) -> list[UpcomingRow]:
    today = today or timezone.localdate()
    horizon = today + timedelta(days=UPCOMING_HORIZON_DAYS)
    rows: list[UpcomingRow] = []

    response_due = (
        active_matters(user)
        .filter(response_deadline__gte=today, response_deadline__lte=horizon)
        .select_related("owner", "stage")
    )
    for matter in response_due.order_by("response_deadline")[:UPCOMING_LIMIT]:
        if matter.response_deadline is None:  # pragma: no cover - excluded by the filter
            continue
        rows.append(
            UpcomingRow(
                when=matter.response_deadline,
                matter=matter,
                owner_name=_owner_name(matter),
                stage_label=_stage_label(matter),
                meaning=MEANING_RESPONSE,
                url=reverse("matters:matter_detail", kwargs={"pk": matter.pk}),
            )
        )

    actions = (
        NextAction.objects.visible_to(user)
        .filter(
            status=ActionStatus.OPEN,
            target_date__gte=today,
            target_date__lte=horizon,
            matter__is_open=True,
            matter__record_mode=RecordMode.FULL,
        )
        .select_related("matter", "matter__owner", "matter__stage")
    )
    for action in actions.order_by("target_date")[:UPCOMING_LIMIT]:
        if action.target_date is None:  # pragma: no cover - excluded by the filter
            continue
        rows.append(
            UpcomingRow(
                when=action.target_date,
                matter=action.matter,
                owner_name=_owner_name(action.matter),
                stage_label=_stage_label(action.matter),
                meaning=_SEMANTICS_MEANING.get(action.date_semantics, MEANING_ACTION),
                url=reverse("matters:matter_detail", kwargs={"pk": action.matter_id}),
            )
        )

    return sorted(rows, key=lambda row: (row.when, row.matter.title))[:UPCOMING_LIMIT]


# ---------------------------------------------------------------------------
# Team and stage distribution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CountRow:
    label: str
    count: int
    url: str


def owner_inventory(user: Any) -> list[CountRow]:
    """Open FULL Matters per owner. **Inventory, never workload.**

    A count of open files says nothing about effort: one may be a two-line
    monitoring note and the next a year of consultation. Naming this workload or
    productivity would invite exactly the comparison the specification forbids
    (18.8), and would make the number worth gaming.
    """
    grouped = (
        active_matters(user)
        .values("owner_id", "owner__display_name")
        .annotate(total=Count("id"))
        .order_by("-total", "owner__display_name")
    )

    rows = [
        CountRow(
            label=entry["owner__display_name"],
            count=entry["total"],
            url=_teemad(olek="avatud", vastutaja=entry["owner_id"]),
        )
        for entry in grouped
        if entry["owner_id"] is not None
    ]

    unassigned = next((entry["total"] for entry in grouped if entry["owner_id"] is None), 0)
    if unassigned:
        rows.append(CountRow(label="Vastutajata", count=unassigned, url=reverse("matters:inbox")))
    return rows


def stage_distribution(user: Any) -> list[CountRow]:
    """Where the visible active files stand in the external process.

    Stage only. A disposition says why Koda stopped working on something, which
    is a different question and does not belong on a bar beside procedural
    stages (specification 3.4).
    """
    grouped = (
        active_matters(user)
        .filter(stage__isnull=False)
        .values("stage__key", "stage__label_et", "stage__sort_order")
        .annotate(total=Count("id"))
        .order_by("stage__sort_order")
    )
    return [
        CountRow(
            label=entry["stage__label_et"],
            count=entry["total"],
            url=_teemad(olek="avatud", hetkeseis=entry["stage__key"]),
        )
        for entry in grouped
    ]


# ---------------------------------------------------------------------------
# Recent incoming
# ---------------------------------------------------------------------------


def recent_incoming(user: Any) -> QuerySet[Matter]:
    """What has arrived lately, newest first.

    Ordered by the date the material was *received* rather than created, so a
    Matter entered late still appears where the department expects it. Rows with
    no received date fall to the end rather than to the top.
    """
    return (
        Matter.objects.visible_to(user)
        .filter(record_mode=RecordMode.FULL)
        .select_related("owner", "source_organisation", "stage")
        .order_by(F("received_date").desc(nulls_last=True), "-created_at")[:RECENT_INCOMING_LIMIT]
    )


# ---------------------------------------------------------------------------
# The whole page
# ---------------------------------------------------------------------------


@dataclass
class Dashboard:
    cards: list[SummaryCard] = field(default_factory=list)
    attention: list[AttentionRow] = field(default_factory=list)
    upcoming: list[UpcomingRow] = field(default_factory=list)
    owners: list[CountRow] = field(default_factory=list)
    stages: list[CountRow] = field(default_factory=list)
    incoming: list[Matter] = field(default_factory=list)

    @property
    def attention_count(self) -> int:
        return len(self.attention)


def build_dashboard(user: Any, today: date | None = None) -> Dashboard:
    today = today or timezone.localdate()
    return Dashboard(
        cards=summary_cards(user, today),
        attention=attention_rows(user, today),
        upcoming=upcoming_rows(user, today),
        owners=owner_inventory(user),
        stages=stage_distribution(user),
        incoming=list(recent_incoming(user)),
    )
