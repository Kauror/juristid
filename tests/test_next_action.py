"""`Järgmiseks` semantics.

The point of this model is that a date means different things depending on what
kind of action carries it. These tests hold that line: only DO + DEADLINE can be
late, and everything else is due for a look at most.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.matters.services import close_matter
from app.workflow.enums import ActionKind, ActionStatus, DateSemantics
from app.workflow.models import NextAction
from app.workflow.services import (
    cancel_next_action,
    complete_next_action,
    current_next_action,
    set_next_action,
)
from tests import factories

pytestmark = pytest.mark.django_db


def _tomorrow():
    return timezone.localdate() + timedelta(days=1)


def _yesterday():
    return timezone.localdate() - timedelta(days=1)


# -- one open action --------------------------------------------------------


def test_setting_an_action_supersedes_the_previous_one(normal_matter, specialist):
    first = set_next_action(
        matter=normal_matter, text="Koosta arvamus", actor=specialist, target_date=_tomorrow()
    )
    second = set_next_action(
        matter=normal_matter, text="Ootan vastust", kind=ActionKind.WAIT, actor=specialist
    )

    first.refresh_from_db()
    assert first.status == ActionStatus.SUPERSEDED
    assert first.replaced_by == second
    assert first.ended_at is not None
    assert current_next_action(normal_matter) == second


def test_only_one_open_action_can_exist_per_matter(normal_matter):
    factories.NextActionFactory(matter=normal_matter, target_date=_tomorrow())
    with pytest.raises(IntegrityError), transaction.atomic():
        factories.NextActionFactory(matter=normal_matter, target_date=_tomorrow())


def test_history_survives_replacement(normal_matter, specialist):
    set_next_action(matter=normal_matter, text="Esimene", actor=specialist, target_date=_tomorrow())
    set_next_action(matter=normal_matter, text="Teine", actor=specialist, target_date=_tomorrow())
    set_next_action(matter=normal_matter, text="Kolmas", actor=specialist, target_date=_tomorrow())

    assert NextAction.objects.filter(matter=normal_matter).count() == 3
    assert NextAction.objects.filter(matter=normal_matter, status=ActionStatus.OPEN).count() == 1
    texts = set(NextAction.objects.filter(matter=normal_matter).values_list("text", flat=True))
    assert texts == {"Esimene", "Teine", "Kolmas"}


def test_completing_an_action_keeps_it_and_leaves_the_matter_without_one(normal_matter, specialist):
    action = set_next_action(
        matter=normal_matter, text="Saada kiri", actor=specialist, target_date=_tomorrow()
    )
    complete_next_action(action=action, actor=specialist)

    action.refresh_from_db()
    assert action.status == ActionStatus.COMPLETED
    assert action.ended_at is not None
    assert current_next_action(normal_matter) is None


def test_a_completed_action_cannot_be_completed_again(normal_matter, specialist):
    action = set_next_action(
        matter=normal_matter, text="Saada kiri", actor=specialist, target_date=_tomorrow()
    )
    complete_next_action(action=action, actor=specialist)
    with pytest.raises(DomainError):
        complete_next_action(action=action, actor=specialist)


def test_closing_a_matter_ends_its_open_action(normal_matter, specialist):
    """A closed file must not keep sitting in somebody's work list."""
    set_next_action(
        matter=normal_matter, text="Jälgi menetlust", kind=ActionKind.MONITOR, actor=specialist
    )
    close_matter(matter=normal_matter, disposition="COMPLETED", actor=specialist)

    assert current_next_action(normal_matter) is None
    assert NextAction.objects.filter(matter=normal_matter).count() == 1


def test_a_closed_matter_rejects_a_new_action(normal_matter, specialist):
    close_matter(matter=normal_matter, disposition="COMPLETED", actor=specialist)
    with pytest.raises(DomainError):
        set_next_action(matter=normal_matter, text="Veel midagi", actor=specialist)


# -- date semantics ---------------------------------------------------------


def test_do_with_a_passed_deadline_is_overdue(normal_matter, specialist):
    action = set_next_action(
        matter=normal_matter,
        text="Koosta arvamus",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=_yesterday(),
        actor=specialist,
    )
    assert action.is_overdue() is True
    assert action.is_due_for_review() is False
    assert action in NextAction.objects.overdue()


def test_wait_with_a_passed_review_date_is_not_overdue(normal_matter, specialist):
    """Waiting on a ministry is the normal state of this work, not a failure."""
    action = set_next_action(
        matter=normal_matter,
        text="Ootan ministeeriumi sõnastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=_yesterday(),
        actor=specialist,
    )
    assert action.is_overdue() is False
    assert action.is_due_for_review() is True
    assert action not in NextAction.objects.overdue()
    assert action in NextAction.objects.due_for_review()


def test_monitor_with_a_passed_date_is_due_for_review_only(normal_matter, specialist):
    action = set_next_action(
        matter=normal_matter,
        text="Kontrolli rakendusakte",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=_yesterday(),
        actor=specialist,
    )
    assert action.is_overdue() is False
    assert action.is_due_for_review() is True


def test_expected_around_is_never_overdue(normal_matter, specialist):
    """An estimate of someone else's timing is not a commitment Koda missed."""
    action = set_next_action(
        matter=normal_matter,
        text="Eelnõu jõuab eeldatavasti valitsusse",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        target_date=_yesterday(),
        actor=specialist,
    )
    assert action.is_overdue() is False


def test_a_do_deadline_in_the_future_is_not_overdue(normal_matter, specialist):
    action = set_next_action(
        matter=normal_matter,
        text="Koosta arvamus",
        target_date=_tomorrow(),
        actor=specialist,
    )
    assert action.is_overdue() is False


def test_the_service_refuses_a_deadline_without_a_date(normal_matter, specialist):
    """A deadline with no date cannot be met, missed or planned against."""
    with pytest.raises(DomainError):
        set_next_action(
            matter=normal_matter,
            text="Millalgi",
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=None,
            actor=specialist,
        )


def test_the_database_refuses_a_deadline_without_a_date(normal_matter, specialist):
    """The service is not the only way in; an importer bypasses it."""
    with pytest.raises(IntegrityError), transaction.atomic():
        NextAction.objects.create(
            matter=normal_matter,
            text="Otse loodud",
            kind=ActionKind.DO,
            date_semantics=DateSemantics.DEADLINE,
            target_date=None,
        )


def test_a_bulk_update_cannot_clear_a_deadline_date(normal_matter, specialist):
    action = set_next_action(
        matter=normal_matter, text="Koosta arvamus", actor=specialist, target_date=_tomorrow()
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        NextAction.objects.filter(pk=action.pk).update(target_date=None)


def test_wait_without_a_date_remains_valid(normal_matter, specialist):
    """Waiting with no idea when is an honest state, not an incomplete one."""
    action = set_next_action(
        matter=normal_matter,
        text="Ootan ministeeriumi vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=None,
        actor=specialist,
    )
    assert action.target_date is None
    assert action.is_overdue() is False
    assert action.is_due_for_review() is False


def test_monitor_without_a_date_remains_valid(normal_matter, specialist):
    action = set_next_action(
        matter=normal_matter,
        text="Jälgin rakendamist",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        target_date=None,
        actor=specialist,
    )
    assert action.target_date is None
    assert action.is_overdue() is False


def test_only_do_plus_deadline_can_ever_be_overdue(normal_matter, specialist):
    """Every other combination stays out of the overdue queue by construction."""
    overdue = set_next_action(
        matter=normal_matter,
        text="Tähtajaline",
        kind=ActionKind.DO,
        date_semantics=DateSemantics.DEADLINE,
        target_date=_yesterday(),
        actor=specialist,
    )
    assert overdue.is_overdue() is True

    for kind, semantics in (
        (ActionKind.DO, DateSemantics.REVIEW_ON),
        (ActionKind.DO, DateSemantics.EXPECTED_AROUND),
        (ActionKind.WAIT, DateSemantics.DEADLINE),
        (ActionKind.WAIT, DateSemantics.REVIEW_ON),
        (ActionKind.WAIT, DateSemantics.EXPECTED_AROUND),
        (ActionKind.MONITOR, DateSemantics.DEADLINE),
        (ActionKind.MONITOR, DateSemantics.REVIEW_ON),
        (ActionKind.MONITOR, DateSemantics.EXPECTED_AROUND),
    ):
        action = set_next_action(
            matter=normal_matter,
            text=f"{kind} {semantics}",
            kind=kind,
            date_semantics=semantics,
            target_date=_yesterday(),
            actor=specialist,
        )
        assert action.is_overdue() is False, (kind, semantics)
    assert NextAction.objects.overdue().count() == 0


def test_date_labels_distinguish_the_three_meanings(normal_matter, specialist):
    """The same date must not read identically in all three cases."""
    labels = set()
    for semantics in DateSemantics.values:
        action = set_next_action(
            matter=normal_matter,
            text="Tekst",
            kind=ActionKind.WAIT,
            date_semantics=semantics,
            target_date=_tomorrow(),
            actor=specialist,
        )
        labels.add(action.date_label)
    assert len(labels) == 3


# -- responsibility, validation and audit -----------------------------------


def test_responsibility_defaults_to_the_matter_owner(normal_matter, specialist):
    action = set_next_action(
        matter=normal_matter, text="Koosta arvamus", actor=specialist, target_date=_tomorrow()
    )
    assert action.responsible == normal_matter.owner


def test_responsibility_can_be_given_to_someone_else(normal_matter, specialist, other_specialist):
    action = set_next_action(
        matter=normal_matter,
        text="Koosta arvamus",
        actor=specialist,
        responsible=other_specialist,
        target_date=_tomorrow(),
    )
    assert action.responsible == other_specialist


def test_empty_text_is_refused(normal_matter, specialist):
    with pytest.raises(DomainError):
        set_next_action(matter=normal_matter, text="   ", actor=specialist)


def test_an_unknown_kind_is_refused(normal_matter, specialist):
    with pytest.raises(DomainError):
        set_next_action(matter=normal_matter, text="Tekst", kind="SOMETHING", actor=specialist)


def test_setting_and_completing_are_both_audited(normal_matter, specialist):
    action = set_next_action(
        matter=normal_matter, text="Koosta arvamus", actor=specialist, target_date=_tomorrow()
    )
    complete_next_action(action=action, actor=specialist)

    types = list(
        ChangeEvent.objects.filter(matter=normal_matter).values_list("event_type", flat=True)
    )
    assert ChangeEventType.NEXT_ACTION_SET in types
    assert ChangeEventType.NEXT_ACTION_COMPLETED in types


def test_cancelling_records_the_reason(normal_matter, specialist):
    action = set_next_action(
        matter=normal_matter, text="Koosta arvamus", actor=specialist, target_date=_tomorrow()
    )
    cancel_next_action(action=action, actor=specialist, reason="Ei ole enam vaja")

    event = ChangeEvent.objects.get(event_type=ChangeEventType.NEXT_ACTION_CANCELLED)
    assert event.payload["reason"] == "Ei ole enam vaja"
