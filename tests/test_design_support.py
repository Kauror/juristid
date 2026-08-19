"""The backend the CVI design package depends on.

Five things the mockups render that the domain had to be able to answer: an
honest fuzzy date, a review that is not a completion, the addressee/teadmiseks
distinction, joint-submitter confirmation, and "this Matter has no next step".
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core.errors import DomainError
from app.matters import selectors
from app.matters.services import close_matter
from app.submissions.enums import RecipientRole
from app.submissions.models import SubmissionJointSubmitter, SubmissionRecipient
from app.submissions.services import (
    addressees_of,
    confirm_joint_submitter,
    create_submission,
    set_recipients,
)
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from app.workflow.services import acknowledge_review, set_next_action
from tests import factories

pytestmark = pytest.mark.django_db


def _days(offset: int) -> date:
    return timezone.localdate() + timedelta(days=offset)


# ---------------------------------------------------------------------------
# A date is rendered at the precision it was actually known to
# ---------------------------------------------------------------------------


def test_an_exact_date_renders_as_a_day(normal_matter, specialist):
    action = set_next_action(
        matter=normal_matter,
        text="Koosta arvamus",
        target_date=date(2026, 8, 21),
        actor=specialist,
    )
    assert action.display_date == "21.08.2026"
    assert action.is_approximate is False


def test_a_month_precision_date_names_the_month(normal_matter, specialist):
    """September 2026 is honest; 01.09.2026 invents a day nobody chose."""
    action = set_next_action(
        matter=normal_matter,
        text="Ootan rakendusakte",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        target_date=date(2026, 9, 1),
        date_precision=DatePrecision.MONTH,
        actor=specialist,
    )
    assert action.display_date == "september 2026"
    assert action.is_approximate is True


def test_a_quarter_precision_date_names_the_quarter(normal_matter, specialist):
    action = set_next_action(
        matter=normal_matter,
        text="Jälgin ELi määruse rakendusakte",
        kind=ActionKind.MONITOR,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        target_date=date(2026, 11, 15),
        date_precision=DatePrecision.QUARTER,
        actor=specialist,
    )
    assert action.display_date == "IV kvartal 2026"


def test_half_year_and_year_precision(normal_matter, specialist):
    half = set_next_action(
        matter=normal_matter,
        text="Poolaasta",
        kind=ActionKind.MONITOR,
        target_date=date(2026, 2, 1),
        date_precision=DatePrecision.HALF_YEAR,
        actor=specialist,
    )
    assert half.display_date == "I poolaasta 2026"

    year = set_next_action(
        matter=normal_matter,
        text="Aasta",
        kind=ActionKind.MONITOR,
        target_date=date(2026, 5, 1),
        date_precision=DatePrecision.YEAR,
        actor=specialist,
    )
    assert year.display_date == "2026"


def test_a_dateless_action_renders_nothing(normal_matter, specialist):
    action = set_next_action(
        matter=normal_matter, text="Ootan", kind=ActionKind.WAIT, actor=specialist
    )
    assert action.display_date == ""


# ---------------------------------------------------------------------------
# Reviewing is not completing
# ---------------------------------------------------------------------------


def test_acknowledging_a_review_moves_the_date_and_keeps_the_action(normal_matter, specialist):
    """The Matter is still waiting on the same thing, so the action survives."""
    action = set_next_action(
        matter=normal_matter,
        text="Ootan ministeeriumi vastust",
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.REVIEW_ON,
        target_date=_days(-3),
        actor=specialist,
    )
    assert action.is_due_for_review() is True

    acknowledge_review(action=action, actor=specialist, next_review_date=_days(14))

    action.refresh_from_db()
    assert action.status == ActionStatus.OPEN
    assert action.target_date == _days(14)
    assert action.is_due_for_review() is False
    assert ChangeEvent.objects.filter(event_type=ChangeEventType.NEXT_ACTION_REVIEWED).exists()


def test_a_review_can_clear_the_date_entirely(normal_matter, specialist):
    action = set_next_action(
        matter=normal_matter,
        text="Jälgin",
        kind=ActionKind.MONITOR,
        target_date=_days(-1),
        actor=specialist,
    )
    acknowledge_review(action=action, actor=specialist, next_review_date=None)
    action.refresh_from_db()
    assert action.target_date is None
    assert action.status == ActionStatus.OPEN


def test_a_do_action_cannot_be_reviewed(normal_matter, specialist):
    """A deadline is met or missed; it is not "checked on"."""
    action = set_next_action(
        matter=normal_matter, text="Koosta arvamus", target_date=_days(3), actor=specialist
    )
    with pytest.raises(DomainError):
        acknowledge_review(action=action, actor=specialist, next_review_date=_days(10))


def test_a_closed_action_cannot_be_reviewed(normal_matter, specialist):
    action = set_next_action(
        matter=normal_matter, text="Ootan", kind=ActionKind.WAIT, actor=specialist
    )
    close_matter(matter=normal_matter, disposition="COMPLETED", actor=specialist)
    action.refresh_from_db()
    with pytest.raises(DomainError):
        acknowledge_review(action=action, actor=specialist)


# ---------------------------------------------------------------------------
# Addressee is not the same as "teadmiseks"
# ---------------------------------------------------------------------------


def test_addressees_and_information_recipients_are_kept_apart(normal_matter, specialist):
    ministry = factories.OrganisationFactory(name="Rahandusministeerium")
    committee = factories.OrganisationFactory(name="Riigikogu majanduskomisjon")

    submission = create_submission(
        matter=normal_matter,
        title="Koja arvamus",
        actor=specialist,
        recipients=[ministry],
        for_information=[committee],
    )

    roles = {
        row.organisation.name: row.role
        for row in SubmissionRecipient.objects.filter(submission=submission)
    }
    assert roles == {
        "Rahandusministeerium": RecipientRole.ADDRESSEE,
        "Riigikogu majanduskomisjon": RecipientRole.FOR_INFORMATION,
    }
    # Only the addressees answer "who did Koda formally write to".
    assert [organisation.name for organisation in addressees_of(submission)] == [
        "Rahandusministeerium"
    ]


def test_the_same_organisation_cannot_be_both(normal_matter, specialist):
    ministry = factories.OrganisationFactory()
    submission = create_submission(matter=normal_matter, title="Arvamus", actor=specialist)

    with pytest.raises(DomainError):
        set_recipients(
            submission=submission,
            addressees=[ministry],
            for_information=[ministry],
            actor=specialist,
        )


def test_an_organisation_appears_once_per_submission(normal_matter, specialist):
    ministry = factories.OrganisationFactory()
    submission = create_submission(
        matter=normal_matter, title="Arvamus", actor=specialist, recipients=[ministry]
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        SubmissionRecipient.objects.create(submission=submission, organisation=ministry)


def test_changing_recipients_is_audited(normal_matter, specialist):
    ministry = factories.OrganisationFactory()
    submission = create_submission(matter=normal_matter, title="Arvamus", actor=specialist)
    set_recipients(submission=submission, addressees=[ministry], actor=specialist)

    assert ChangeEvent.objects.filter(
        event_type=ChangeEventType.SUBMISSION_RECIPIENTS_CHANGED
    ).exists()


# ---------------------------------------------------------------------------
# A joint letter is only joint once the co-signatory agrees
# ---------------------------------------------------------------------------


def test_a_joint_submitter_starts_unconfirmed(normal_matter, specialist):
    partner = factories.OrganisationFactory(name="EVEA")
    submission = create_submission(
        matter=normal_matter,
        title="Ühispöördumine",
        actor=specialist,
        joint_submitters=[partner],
    )
    row = SubmissionJointSubmitter.objects.get(submission=submission)
    assert row.confirmed is False
    assert row.confirmed_at is None


def test_confirming_a_joint_submitter_records_when(normal_matter, specialist):
    partner = factories.OrganisationFactory()
    submission = create_submission(
        matter=normal_matter, title="Ühispöördumine", actor=specialist, joint_submitters=[partner]
    )
    row = confirm_joint_submitter(submission=submission, organisation=partner, actor=specialist)
    assert row.confirmed is True
    assert row.confirmed_at is not None


def test_the_database_refuses_a_confirmation_without_a_timestamp(normal_matter, specialist):
    partner = factories.OrganisationFactory()
    submission = create_submission(
        matter=normal_matter, title="Ühispöördumine", actor=specialist, joint_submitters=[partner]
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        SubmissionJointSubmitter.objects.filter(submission=submission).update(confirmed=True)


# ---------------------------------------------------------------------------
# "This Matter has no next step" is a query, not a guess
# ---------------------------------------------------------------------------


def test_matters_without_next_action_finds_the_quiet_ones(specialist):
    quiet = factories.MatterFactory(owner=specialist)
    busy = factories.MatterFactory(owner=specialist)
    set_next_action(matter=busy, text="Tegevus", target_date=_days(3), actor=specialist)

    found = set(selectors.matters_without_next_action(specialist).values_list("id", flat=True))
    assert quiet.id in found
    assert busy.id not in found


def test_a_closed_matter_is_not_flagged_as_missing_a_next_step(specialist):
    """Closing a Matter ends its action; that is not a gap to chase."""
    matter = factories.MatterFactory(owner=specialist)
    close_matter(matter=matter, disposition="COMPLETED", actor=specialist)

    found = set(selectors.matters_without_next_action(specialist).values_list("id", flat=True))
    assert matter.id not in found


def test_an_archive_matter_is_not_flagged_either(specialist):
    """Archive rows never had a next step and are not supposed to acquire one."""
    archive = factories.ArchiveMatterFactory()
    found = set(selectors.matters_without_next_action(specialist).values_list("id", flat=True))
    assert archive.id not in found


def test_the_query_respects_authorization(specialist, other_specialist):
    from app.core.enums import Visibility

    factories.MatterFactory(owner=other_specialist, visibility=Visibility.RESTRICTED)
    assert selectors.matters_without_next_action(specialist).count() == 0
