"""A code-only rollback to r92 after the Riigikogu was switched on (coordinator item 8).

r92 (e344a474) knows only two informational kinds of `Oluline tähtaeg`:

    app/intelligence/enums.py:114-116 (e344a474)
    INFORMATIONAL_IMPORTANT_DATE_KINDS = frozenset(
        {ImportantDateKind.PLANNED_COMMENCEMENT.value,
         ImportantDateKind.ESTIMATED_COMMENCEMENT.value}
    )

Every rule that would read a parliamentary date as a live deadline is unchanged from r92
to r93 except for that set (`git diff e344a474 -- app/matters/next_step.py
app/notifications/categories.py app/matters/closure_work.py` is empty; work_items.py
changes only fields unrelated to milestones). So the r92 semantics are the r93 functions
with the set put back to r92's value (`r92_rules`), and every test below first shows the
hazard is real under those rules before it shows the guard removes it.

The three scenarios: before activation, after discovery but before a date was accepted,
and after a lawyer accepted a parliamentary date onto a Teema.
"""

from __future__ import annotations

import datetime as dt
import uuid as uuid_module

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.forms.models import model_to_dict
from django.utils import timezone

from app.audit.enums import SecurityEventType
from app.audit.models import SecurityAuditEvent
from app.intelligence.enums import FactStatus, ImportantDateKind
from app.intelligence.models import MatterImportantDate
from app.register_watch.models import TargetOrigin, WatchSource, WatchTarget
from app.riigikogu import invitations
from app.riigikogu.enums import DeadlineKind, DocumentKind, ExtractionStatus, InvitationReview
from app.riigikogu.management.commands import riigikogu_rollback_guard as guard
from app.riigikogu.models import Bill, ExternalDeadline, Invitation, ParliamentDocument
from tests import factories

pytestmark = pytest.mark.django_db

#: r92's set, verbatim (e344a474:app/intelligence/enums.py:114-116).
R92_INFORMATIONAL = frozenset(
    {ImportantDateKind.PLANNED_COMMENCEMENT.value, ImportantDateKind.ESTIMATED_COMMENCEMENT.value}
)
BILL = "5b1d7c0e-2a3b-4c4d-8e5f-6a7b8c9d0e1f"


@pytest.fixture
def r92_rules(monkeypatch):
    """r93's milestone rules with r92's informational set: r92's behaviour exactly."""
    from app.intelligence import enums
    from app.matters import calendar, next_step, work_items

    for module in (enums, next_step, work_items, calendar):
        monkeypatch.setattr(module, "INFORMATIONAL_IMPORTANT_DATE_KINDS", R92_INFORMATIONAL)


def _today() -> dt.date:
    return timezone.localdate()


def _live_under(owner, matter) -> dict[str, bool]:
    """Each r92 surface that would treat an `Oluline tähtaeg` as a live deadline."""
    from app.matters import next_step
    from app.matters import work_items as wi
    from app.matters.closure_work import live_work_at_closure
    from app.notifications.categories import _important_sources

    today = _today()
    items = [
        item
        for item in wi.work_items(owner, today=today)
        if item.source_type == wi.SOURCE_IMPORTANT_DEADLINE and item.matter.pk == matter.pk
    ]
    records = list(
        MatterImportantDate.objects.filter(matter=matter).visible_to(owner)
    )  # what the Teema page passes (`matter_intelligence`)
    return {
        # «Järgmiseks»: next_step.py:117-124 and :91-114 (e344a474)
        "next_action": next_step.upcoming_milestones(owner, today).filter(matter=matter).exists()
        or next_step.upcoming_milestone(records, today) is not None,
        # Minu asjad / Osakond / workload: work_items.py:1066-1088, :1543
        "work_item": bool(items),
        # late work: work_items.py:815 ``is_overdue=record.period_end < today``
        "overdue": any(item.is_overdue for item in items),
        # deadline notifications: notifications/categories.py:171-176, :448-466
        "notification_source": _important_sources(owner).filter(matter=matter).exists(),
        # closure: closure_work.py:135-182 counts no `Oluline tähtaeg` at all
        "closure_hold": not live_work_at_closure(matter).is_empty,
    }


def _accepted_parliamentary_dates(owner):
    """A Teema following a bill, a committee's invitation with a confirmed date, and a
    lawyer's «Lisa tähtajana» through the real service — one future, one past date."""
    matter = factories.MatterFactory(title="Äriseadustiku muudatused", owner=owner)
    bill = Bill.objects.create(uuid=BILL, mark=1082, type_code="SE", title="Äriseadustik")
    WatchTarget.objects.create(
        matter=matter,
        source=WatchSource.RIIGIKOGU,
        identifier=BILL,
        label="1082 SE",
        origin=TargetOrigin.LINKED,
    )
    records = []
    for offset in (10, -3):
        now = timezone.now()
        document = ParliamentDocument.objects.create(
            uuid=uuid_module.uuid4(),
            kind=DocumentKind.INVITATION,
            title="Kaasamiskiri",
            bill=bill,
            first_seen_at=now,
            last_seen_at=now,
        )
        invitation = Invitation.objects.create(document=document, committee="Õiguskomisjon")
        ExternalDeadline.objects.create(
            kind=DeadlineKind.OPINION_REQUEST,
            bill=bill,
            document=document,
            due_on=_today() + dt.timedelta(days=offset),
            extraction=ExtractionStatus.FOUND,
            evidence="Palume arvamust hiljemalt …",
        )
        records.append(invitations.add_as_date(invitation, matter, actor=owner))
    assert {r.kind for r in records} == {ImportantDateKind.RK_OPINION_REQUEST}
    return matter, records


def _snapshot(records) -> list[dict]:
    rows = MatterImportantDate.objects.filter(pk__in=[r.pk for r in records]).order_by("pk")
    return [{**model_to_dict(row), "updated_at": row.updated_at} for row in rows]


# -- scenario 1: before activation ---------------------------------------------------------


def test_before_activation_there_is_nothing_to_park_and_r92_is_safe(specialist):
    factories.MatterFactory(owner=specialist)
    assert guard.check().safe_for_r92
    call_command("riigikogu_rollback_guard", "--check", verbosity=0)
    assert guard.park() == []
    assert not SecurityAuditEvent.objects.filter(detail__action=guard.ACTION_PARK).exists()


# -- scenario 2: after discovery, before a date was accepted --------------------------------


def test_after_discovery_without_an_accepted_date_r92_sees_no_deadline(specialist, r92_rules):
    """Invitations, a followed bill and its parliamentary deadline are r93's own tables:
    r92 reads none of them as work. Only a `MatterImportantDate` could be."""
    matter = factories.MatterFactory(owner=specialist)
    bill = Bill.objects.create(uuid=BILL, mark=1082, type_code="SE", title="Äriseadustik")
    WatchTarget.objects.create(
        matter=matter, source=WatchSource.RIIGIKOGU, identifier=BILL, origin=TargetOrigin.LINKED
    )
    now = timezone.now()
    document = ParliamentDocument.objects.create(
        uuid=uuid_module.uuid4(),
        kind=DocumentKind.INVITATION,
        title="Kiri",
        bill=bill,
        first_seen_at=now,
        last_seen_at=now,
    )
    Invitation.objects.create(document=document)
    ExternalDeadline.objects.create(
        kind=DeadlineKind.OPINION_REQUEST,
        bill=bill,
        document=document,
        due_on=_today() - dt.timedelta(days=2),
        extraction=ExtractionStatus.FOUND,
    )

    assert guard.check().safe_for_r92
    assert not any(_live_under(specialist, matter).values())


# -- scenario 3: after a lawyer accepted a parliamentary date -------------------------------


def test_r92_reads_an_accepted_parliamentary_date_as_a_live_deadline(specialist, r92_rules):
    """The hazard the guard exists for: without parking, r92 makes the informational
    date the next step, late work, workload and a deadline notification source."""
    matter, _records = _accepted_parliamentary_dates(specialist)
    live = _live_under(specialist, matter)
    assert live["next_action"] and live["work_item"] and live["overdue"]
    assert live["notification_source"]
    # r92 never held a closure on an `Oluline tähtaeg`, parked or not.
    assert not live["closure_hold"]
    assert not guard.check().safe_for_r92
    with pytest.raises(CommandError, match="pole ohutu"):
        call_command("riigikogu_rollback_guard", "--check", verbosity=0)


def test_r93_itself_reads_it_as_informational(specialist):
    matter, _records = _accepted_parliamentary_dates(specialist)
    assert not any(_live_under(specialist, matter).values())


def test_after_park_r92_finds_no_next_action_no_overdue_no_hold_no_notification(
    specialist, r92_rules
):
    matter, records = _accepted_parliamentary_dates(specialist)
    other = factories.MatterFactory(owner=specialist)
    ordinary = MatterImportantDate.objects.create(
        matter=other, title="Konsultatsioon", date_value=_today(), period_end=_today()
    )

    call_command("riigikogu_rollback_guard", "--park", verbosity=0)

    assert not any(_live_under(specialist, matter).values())
    call_command("riigikogu_rollback_guard", "--check", verbosity=0)  # exit 0
    # Nothing deleted; an ordinary date is untouched and still live work.
    assert MatterImportantDate.objects.filter(pk__in=[r.pk for r in records]).count() == 2
    assert _live_under(specialist, other)["work_item"]
    ordinary.refresh_from_db()
    assert ordinary.removed_at is None
    # The invitation still names the date it was accepted as.
    assert (
        Invitation.objects.filter(
            review=InvitationReview.DATE_ADDED, important_date__in=records
        ).count()
        == 2
    )
    event = SecurityAuditEvent.objects.get(detail__action=guard.ACTION_PARK)
    assert event.event_type == SecurityEventType.IMPORT_RUN
    assert sorted(event.detail["ids"]) == sorted(str(r.pk) for r in records)


def test_restore_returns_exactly_what_was_parked(specialist):
    _matter, records = _accepted_parliamentary_dates(specialist)
    # One a lawyer removed herself, before any rollback: the guard never touches it.
    removed_by_lawyer = records[1]
    removed_at = timezone.now() - dt.timedelta(days=1)
    MatterImportantDate.objects.filter(pk=removed_by_lawyer.pk).update(
        removed_at=removed_at, removed_by=specialist
    )
    cancelled = records[0]
    MatterImportantDate.objects.filter(pk=cancelled.pk).update(status=FactStatus.CANCELLED)
    before = _snapshot(records)

    assert guard.park() == [str(cancelled.pk)]
    assert guard.park() == []  # idempotent
    assert guard.restore() == [str(cancelled.pk)]
    assert guard.restore() == []

    assert _snapshot(records) == before
    assert guard.check().removed_by_person == 1
    assert SecurityAuditEvent.objects.filter(detail__action=guard.ACTION_RESTORE).count() == 1


def test_after_restore_r93_reads_the_dates_as_before(specialist):
    matter, records = _accepted_parliamentary_dates(specialist)
    before = _snapshot(records)
    call_command("riigikogu_rollback_guard", "--park", verbosity=0)
    call_command("riigikogu_rollback_guard", "--restore", verbosity=0)
    assert _snapshot(records) == before
    assert MatterImportantDate.objects.filter(matter=matter).visible_to(specialist).count() == 2
    assert not any(_live_under(specialist, matter).values())
