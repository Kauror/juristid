"""What Monitooring → Riigikogu and the Teema's «Registritest» show (ADR 0226).

Every Teema named next to a parliamentary record is read through
`Matter.objects.visible_to(user)` first: the public record is public, its tie to
a Teema is not. A row whose bill is tied only to Teemad the reader may not see is
shown as the public record it is, naming no Teema. A handful of queries per page,
however many rows (the bills' Teemad are read in one query, not per row).
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from django.db.models import Q
from django.utils import timezone

from app.riigikogu import agendas, amendments
from app.riigikogu.enums import (
    AnalysisStatus,
    AnalysisType,
    DeadlineKind,
    DocumentKind,
    ExtractionStatus,
    FactState,
    InvitationReview,
    MentionCategory,
    ResponseCheck,
)
from app.riigikogu.models import (
    Analysis,
    Bill,
    CommitteeAgendaItem,
    ExternalDeadline,
    Invitation,
    ParliamentDocument,
    StenogramMention,
)

#: A changed deadline is pointed out this long after it changed.
CHANGED_FOR = dt.timedelta(days=7)
UPCOMING_DAYS = 14


def teemad_by_bill(user: Any, bill_ids: set[Any]) -> dict[Any, list[Any]]:
    """Bill pk → the Teemad tied to it that this reader may see. One query."""
    from app.matters.models import Matter
    from app.register_watch.models import WatchSource, WatchTarget

    if not bill_ids:
        return {}
    uuids = {str(u): pk for pk, u in Bill.objects.filter(pk__in=bill_ids).values_list("pk", "uuid")}
    visible = Matter.objects.visible_to(user).filter(deleted_at__isnull=True)
    found: dict[Any, list[Any]] = defaultdict(list)
    for target in (
        WatchTarget.objects.filter(
            source=WatchSource.RIIGIKOGU,
            active=True,
            identifier__in=list(uuids),
            matter__in=visible,
        )
        .select_related("matter", "matter__owner")
        .order_by("matter__reference_year", "matter__reference_number")
    ):
        found[uuids[target.identifier]].append(target.matter)
    return found


@dataclass
class InvitationRow:
    invitation: Invitation
    deadline: ExternalDeadline | None
    previous: ExternalDeadline | None
    attendance: ExternalDeadline | None
    teemad: list[Any]
    #: Against the Teema's `Arvamuse tähtaeg` (Lovable's): «same», «differs», «none».
    lovable: str = ""
    lovable_date: dt.date | None = None
    changed: bool = False
    candidate: Any = None

    @property
    def needs_check(self) -> bool:
        return self.deadline is None or not self.deadline.verified

    @property
    def status_label(self) -> str:
        if self.deadline is None:
            return ExtractionStatus.NOT_FOUND.label
        return ExtractionStatus(self.deadline.extraction).label


def _lovable(row: InvitationRow) -> None:
    due = row.deadline.due_on if row.deadline else None
    dates = {m.response_deadline for m in row.teemad if getattr(m, "response_deadline", None)}
    if not row.teemad:
        return
    if not dates:
        row.lovable = "none"
    elif due is not None and due in dates and len(dates) == 1:
        row.lovable, row.lovable_date = "same", due
    else:
        row.lovable, row.lovable_date = "differs", sorted(dates)[0]


def invitation_rows(
    user: Any, invitations: list[Invitation], *, now: dt.datetime | None = None
) -> list[InvitationRow]:
    now = now or timezone.now()
    deadlines: dict[Any, dict[str, ExternalDeadline]] = defaultdict(dict)
    superseded: dict[Any, ExternalDeadline] = {}
    documents = [i.document_id for i in invitations]
    for row in ExternalDeadline.objects.filter(document_id__in=documents).select_related(
        "supersedes"
    ):
        if row.state == FactState.ACTIVE:
            deadlines[row.document_id][row.kind] = row
    for kinds in deadlines.values():
        current = kinds.get(DeadlineKind.OPINION_REQUEST)
        if (
            current is not None
            and current.supersedes_id
            and current.created_at >= now - CHANGED_FOR
            and current.supersedes is not None
        ):
            superseded[current.pk] = current.supersedes
    teemad = teemad_by_bill(user, {i.document.bill_id for i in invitations if i.document.bill_id})
    rows: list[InvitationRow] = []
    for invitation in invitations:
        own = deadlines.get(invitation.document_id, {})
        opinion = own.get(DeadlineKind.OPINION_REQUEST)
        previous = superseded.get(opinion.pk) if opinion else None
        moved = previous is not None and opinion is not None and previous.due_on != opinion.due_on
        line = InvitationRow(
            invitation=invitation,
            deadline=opinion,
            previous=previous if moved else None,
            attendance=own.get(DeadlineKind.ATTENDANCE),
            teemad=teemad.get(invitation.document.bill_id, []),
            candidate=invitation.candidate,
        )
        line.changed = line.previous is not None
        _lovable(line)
        rows.append(line)
    return rows


def _sort_key(row: InvitationRow) -> tuple[Any, ...]:
    day = row.deadline.due_on if row.deadline and row.deadline.due_on else dt.date.max
    return (not row.needs_check, not row.changed, day)


@dataclass
class Page:
    open_rows: list[InvitationRow]
    response_rows: list[InvitationRow]
    previous_rows: list[InvitationRow]
    agenda_ours: list[CommitteeAgendaItem]
    agenda_all: list[CommitteeAgendaItem]
    signals: list[CommitteeAgendaItem]
    deadlines_ours: list[ExternalDeadline]
    deadlines_other: list[ExternalDeadline]
    mentions: list[StenogramMention]
    agenda_teemad: dict[Any, list[Any]] = field(default_factory=dict)


def monitooring(user: Any, *, today: dt.date | None = None, everything: bool = False) -> Page:
    """Monitooring → Riigikogu, in the four sections ADR 0226 §2 orders."""
    today = today or timezone.localdate()
    base = Invitation.objects.select_related("document", "document__bill", "candidate").order_by(
        "-document__registered_on"
    )
    open_invitations = list(base.filter(review=InvitationReview.NEW))
    to_check = list(
        base.exclude(review=InvitationReview.NEW).filter(response=ResponseCheck.NEEDS_CHECK)
    )
    previous = list(
        base.exclude(review=InvitationReview.NEW).exclude(response=ResponseCheck.NEEDS_CHECK)[:40]
    )
    rows = invitation_rows(user, open_invitations + to_check + previous)
    by_pk = {row.invitation.pk: row for row in rows}
    open_rows = sorted((by_pk[i.pk] for i in open_invitations), key=_sort_key)
    response_rows = [by_pk[i.pk] for i in to_check]
    previous_rows = [by_pk[i.pk] for i in previous]

    upcoming = [item for item in agendas.upcoming(days=UPCOMING_DAYS) if not item.routine]
    teemad = teemad_by_bill(user, {item.bill_id for item in upcoming if item.bill_id})
    ours = [item for item in upcoming if item.bill_id in teemad]
    signals = [item for item in upcoming if item.signal]
    invited_bills = {
        row.invitation.document.bill_id for row in open_rows if row.invitation.document.bill_id
    }
    candidate_bills = _candidate_bill_ids()
    all_deadlines = list(amendments.upcoming(today=today))
    deadline_teemad = teemad_by_bill(user, {d.bill_id for d in all_deadlines if d.bill_id})
    teemad.update(deadline_teemad)
    priority = set(deadline_teemad) | invited_bills | candidate_bills
    # What each row names, read once above: a Teema the reader may see, or none.
    for row in [*upcoming, *all_deadlines]:
        row.teemad_shown = teemad.get(row.bill_id, [])
    mentions = list(
        StenogramMention.objects.filter(
            category=MentionCategory.OTHER, source="STENOGRAM"
        ).order_by("-sitting_on")[:20]
    )
    return Page(
        open_rows=open_rows,
        response_rows=response_rows,
        previous_rows=previous_rows,
        agenda_ours=ours,
        agenda_all=upcoming if everything else [],
        signals=signals,
        deadlines_ours=[d for d in all_deadlines if d.bill_id in priority],
        deadlines_other=[d for d in all_deadlines if d.bill_id not in priority],
        mentions=mentions,
        agenda_teemad=teemad,
    )


def _candidate_bill_ids() -> set[Any]:
    from app.register_watch.models import CandidateItem, CandidateStatus, WatchSource

    keys = [
        external.removeprefix("draft:")
        for external in CandidateItem.objects.filter(
            source=WatchSource.RIIGIKOGU, candidate__status=CandidateStatus.NEW
        ).values_list("external_id", flat=True)
    ]
    valid = []
    for key in keys:
        try:
            import uuid as uuid_module

            valid.append(uuid_module.UUID(key))
        except ValueError:
            continue
    return set(Bill.objects.filter(uuid__in=valid).values_list("pk", flat=True))


def counts(user: Any) -> int:
    """For the Monitooring tab badge: invitations waiting for a decision."""
    return Invitation.objects.filter(review=InvitationReview.NEW).count()


# -- the Teema --------------------------------------------------------------------------


@dataclass
class TeemaParliament:
    bills: list[Bill]
    targets: list[Any]
    invitations: list[InvitationRow]
    amendment_deadlines: list[ExternalDeadline]
    sittings: list[CommitteeAgendaItem]
    mentions: list[StenogramMention]
    opinions: list[ParliamentDocument]
    analyses: dict[str, list[Analysis]]
    proposals: list[Any] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.bills or self.invitations)

    @property
    def sittings_count(self) -> int:
        return len(self.sittings) + len(self.mentions)


def teema(user: Any, matter: Any) -> TeemaParliament:
    """The Teema's parliamentary sections: only its own tied bills, nothing guessed."""
    from app.riigikogu import analysis as document_ai
    from app.riigikogu import bills as bill_service

    targets = list(bill_service.linked_targets(matter).order_by("created_at"))
    keys = []
    for target in targets:
        try:
            import uuid as uuid_module

            keys.append(uuid_module.UUID(target.identifier))
        except ValueError:
            continue
    tied = list(Bill.objects.filter(uuid__in=keys))
    ids = [b.pk for b in tied]
    invitations = list(
        Invitation.objects.filter(document__bill_id__in=ids)
        .select_related("document", "document__bill", "candidate")
        .order_by("-document__registered_on")
    )
    rows = invitation_rows(user, invitations)
    now = timezone.now()
    analyses: dict[str, list[Analysis]] = defaultdict(list)
    # Nothing while document AI is off; never a mocked analysis in production.
    for analysis in (
        document_ai.shown(Analysis.objects.all())
        .filter(bill_id__in=ids, status=AnalysisStatus.DONE)
        .order_by("-created_at")
    ):
        analyses[analysis.analysis_type].append(analysis)
    return TeemaParliament(
        bills=tied,
        targets=targets,
        invitations=rows,
        amendment_deadlines=list(
            ExternalDeadline.objects.filter(
                bill_id__in=ids,
                kind=DeadlineKind.AMENDMENTS,
                state=FactState.ACTIVE,
                document__isnull=True,
            ).select_related("bill")
        ),
        sittings=list(
            CommitteeAgendaItem.objects.filter(bill_id__in=ids)
            .filter(Q(sitting_at__gte=now - dt.timedelta(days=60)))
            .order_by("-sitting_at")[:20]
        ),
        mentions=list(
            StenogramMention.objects.filter(bill_id__in=ids).order_by("-sitting_on")[:20]
        ),
        opinions=list(
            ParliamentDocument.objects.filter(
                bill_id__in=ids, kind__in=[DocumentKind.OTHER_OPINION, DocumentKind.KODA_OPINION]
            ).order_by("-registered_on")[:40]
        ),
        analyses={AnalysisType(key).value: value for key, value in analyses.items()},
    )
