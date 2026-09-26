"""The rules the forms repeat are the services' own (ENG-043).

A form is what one browser was shown; a service is what every writer goes
through. Four invariants were enforced only in the first:

* a `Kaasamine`'s reply-by date could be recorded before the round began;
* a sent opinion could be recorded as sent tomorrow;
* a `NextAction` could store a precision nobody can render — `'BOGUS'`, or
  `QUARTER` on a step with no date at all;
* and `workflow_nextaction` had no `CHECK` for any of its four vocabularies,
  where every sibling precision column has one.

Each is asserted here at the service, with the refusal writing no row and no
audit event, and the structural ones again at the database. The preflight that
reports rows already breaking them — before the migration that installs the
`CHECK`s is run — is asserted here too, because a constraint migration whose
existing-data question nobody can answer is a production outage waiting for a
deployment day.

Every date is fixed. Today is frozen at `TODAY` through `timezone.localdate`,
and nothing here reads the real calendar.
"""

from __future__ import annotations

import datetime
import io
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.core import deployment
from app.core.errors import DomainError
from app.documents.enums import DocumentRole
from app.matters import services as matter_services
from app.matters.models import MatterEngagement
from app.submissions import services as submission_services
from app.submissions.enums import SentAtPrecision, SubmissionStatus
from app.submissions.models import Submission
from app.workflow import services as workflow_services
from app.workflow.enums import ActionKind, DatePrecision, DateSemantics
from app.workflow.models import NextAction
from tests import factories

pytestmark = pytest.mark.django_db

TALLINN = ZoneInfo("Europe/Tallinn")

#: The business day every test here lives on. A Thursday, so nothing depends on
#: a weekend, and far enough from a month end that «tomorrow» is ordinary.
TODAY = datetime.date(2026, 9, 24)
TOMORROW = TODAY + datetime.timedelta(days=1)

_REAL_LOCALDATE = timezone.localdate


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    """`timezone.localdate()` answers `TODAY`; converting a value still converts.

    Only the no-argument call is frozen. `timezone.localdate(value)` is how a
    stored instant is read as a Tallinn business day, and replacing that with a
    constant would make every send look as if it happened today.
    """

    def frozen(value=None, timezone=None):
        if value is None:
            return TODAY
        return _REAL_LOCALDATE(value, timezone)

    monkeypatch.setattr("django.utils.timezone.localdate", frozen)
    return TODAY


def _tallinn(day: datetime.date, hour: int = 0, minute: int = 0) -> datetime.datetime:
    return datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=TALLINN)


def _events(event_type: str) -> int:
    return ChangeEvent.objects.filter(event_type=event_type).count()


# ---------------------------------------------------------------------------
# 1. A reply-by date cannot precede the round it belongs to
# ---------------------------------------------------------------------------


def _add(matter, actor, **kwargs):
    return matter_services.add_engagement(
        matter=matter,
        kind=kwargs.pop("kind", "OTHER"),
        title="Kaasati liikmeid",
        actor=actor,
        **kwargs,
    )


def test_add_engagement_refuses_a_deadline_before_the_round(normal_matter, specialist):
    before = _events(ChangeEventType.ENGAGEMENT_ADDED)

    with pytest.raises(DomainError) as refusal:
        _add(
            normal_matter,
            specialist,
            occurred_on=datetime.date(2026, 9, 10),
            feedback_deadline=datetime.date(2026, 9, 9),
        )

    assert str(refusal.value) == matter_services.DEADLINE_BEFORE_ENGAGEMENT
    assert not MatterEngagement.objects.filter(matter=normal_matter).exists()
    assert _events(ChangeEventType.ENGAGEMENT_ADDED) == before


def test_the_same_day_and_a_later_day_are_ordinary(normal_matter, specialist):
    _add(
        normal_matter,
        specialist,
        occurred_on=datetime.date(2026, 9, 10),
        feedback_deadline=datetime.date(2026, 9, 10),
    )
    _add(
        normal_matter,
        specialist,
        occurred_on=datetime.date(2026, 9, 10),
        feedback_deadline=datetime.date(2026, 9, 30),
    )
    # And either date alone relates to nothing.
    _add(normal_matter, specialist, feedback_deadline=datetime.date(2026, 1, 1))
    _add(normal_matter, specialist, occurred_on=datetime.date(2026, 9, 10))
    assert MatterEngagement.objects.filter(matter=normal_matter).count() == 4


@pytest.mark.parametrize(
    ("occurred_on", "precision", "deadline", "refused"),
    [
        # *oktoober 2025*: a deadline inside the month is the commonest thing a
        # round run over a month says; one before the month began is a slip.
        (datetime.date(2025, 10, 1), DatePrecision.MONTH, datetime.date(2025, 10, 15), False),
        (datetime.date(2025, 10, 1), DatePrecision.MONTH, datetime.date(2025, 9, 30), True),
        # *IV kvartal 2025*
        (datetime.date(2025, 10, 1), DatePrecision.QUARTER, datetime.date(2025, 11, 30), False),
        (datetime.date(2025, 10, 1), DatePrecision.QUARTER, datetime.date(2025, 9, 30), True),
        # *2026*
        (datetime.date(2026, 1, 1), DatePrecision.YEAR, datetime.date(2026, 3, 1), False),
        (datetime.date(2026, 1, 1), DatePrecision.YEAR, datetime.date(2025, 12, 31), True),
        # *II poolaasta 2026*, historical but still a period.
        (datetime.date(2026, 7, 1), DatePrecision.HALF_YEAR, datetime.date(2026, 8, 1), False),
        (datetime.date(2026, 7, 1), DatePrecision.HALF_YEAR, datetime.date(2026, 6, 30), True),
        # **The period, not the stored number.** A caller that hands in
        # 20 October at `MONTH` has named October; a reply-by date of 5 October
        # falls inside it, although it is before the raw value.
        (datetime.date(2025, 10, 20), DatePrecision.MONTH, datetime.date(2025, 10, 5), False),
        # `INFERRED` is a day (docs/adr/0079 §8).
        (datetime.date(2025, 10, 20), DatePrecision.INFERRED, datetime.date(2025, 10, 19), True),
    ],
)
def test_an_approximate_round_refuses_only_a_deadline_before_its_whole_period(
    normal_matter, specialist, occurred_on, precision, deadline, refused
):
    """docs/adr/0079: a period covers its days. «Cannot precede» therefore means
    the reply-by day (always exact, docs/adr/0079 §11) falls before the first day
    the round could have happened — never before the stored anchor as a number."""
    kwargs = {
        "occurred_on": occurred_on,
        "occurred_on_precision": precision,
        "feedback_deadline": deadline,
    }
    if refused:
        with pytest.raises(DomainError):
            _add(normal_matter, specialist, **kwargs)
        assert not MatterEngagement.objects.filter(matter=normal_matter).exists()
    else:
        _add(normal_matter, specialist, **kwargs)
        assert MatterEngagement.objects.filter(matter=normal_matter).count() == 1


def test_correct_engagement_refuses_moving_the_round_past_its_deadline(normal_matter, specialist):
    """The auditor's second case: the dates were fine until a correction moved one."""
    engagement = _add(
        normal_matter,
        specialist,
        occurred_on=datetime.date(2026, 9, 1),
        feedback_deadline=datetime.date(2026, 9, 15),
    )
    before = _events(ChangeEventType.ENGAGEMENT_CHANGED)

    with pytest.raises(DomainError) as refusal:
        matter_services.correct_engagement(
            engagement=engagement, occurred_on=datetime.date(2026, 9, 20), actor=specialist
        )
    assert str(refusal.value) == matter_services.DEADLINE_BEFORE_ENGAGEMENT

    with pytest.raises(DomainError):
        matter_services.correct_engagement(
            engagement=engagement, feedback_deadline=datetime.date(2026, 8, 31), actor=specialist
        )

    with pytest.raises(DomainError):
        # A precision that moves the period's start past the deadline.
        matter_services.correct_engagement(
            engagement=engagement,
            occurred_on=datetime.date(2026, 10, 1),
            occurred_on_precision=DatePrecision.QUARTER,
            actor=specialist,
        )

    engagement.refresh_from_db()
    assert engagement.occurred_on == datetime.date(2026, 9, 1)
    assert engagement.feedback_deadline == datetime.date(2026, 9, 15)
    assert engagement.occurred_on_precision == DatePrecision.EXACT
    assert _events(ChangeEventType.ENGAGEMENT_CHANGED) == before


def test_a_correction_that_keeps_the_pair_valid_is_ordinary(normal_matter, specialist):
    engagement = _add(
        normal_matter,
        specialist,
        occurred_on=datetime.date(2026, 9, 1),
        feedback_deadline=datetime.date(2026, 9, 15),
    )

    matter_services.correct_engagement(
        engagement=engagement,
        occurred_on=datetime.date(2026, 9, 1),
        occurred_on_precision=DatePrecision.MONTH,
        feedback_deadline=datetime.date(2026, 9, 3),
        actor=specialist,
    )

    engagement.refresh_from_db()
    assert engagement.occurred_on_precision == DatePrecision.MONTH
    assert engagement.feedback_deadline == datetime.date(2026, 9, 3)


def test_a_correction_that_moves_neither_date_is_not_blocked_by_an_old_row(
    normal_matter, specialist
):
    """A row that already holds the impossible pair — written before the rule
    reached the service — is the preflight's finding, not a row frozen solid: a
    correction of its title moves neither date and cannot make the pair worse."""
    engagement = _add(normal_matter, specialist, occurred_on=datetime.date(2026, 9, 10))
    MatterEngagement.objects.filter(pk=engagement.pk).update(
        feedback_deadline=datetime.date(2026, 9, 1)
    )

    matter_services.correct_engagement(
        engagement=engagement, title="Kaasati tööstusettevõtteid", actor=specialist
    )

    engagement.refresh_from_db()
    assert engagement.title == "Kaasati tööstusettevõtteid"


def test_opening_a_wait_uses_the_same_period_rule(normal_matter, specialist):
    """`Ootan tagasisidet` had its own copy comparing the raw anchor. One rule now."""
    engagement = _add(
        normal_matter,
        specialist,
        occurred_on=datetime.date(2025, 10, 20),
        occurred_on_precision=DatePrecision.MONTH,
    )

    matter_services.open_engagement_feedback_wait(
        engagement=engagement, deadline=datetime.date(2025, 10, 5), actor=specialist
    )

    other = _add(normal_matter, specialist, occurred_on=datetime.date(2025, 10, 20))
    with pytest.raises(DomainError) as refusal:
        matter_services.open_engagement_feedback_wait(
            engagement=other, deadline=datetime.date(2025, 10, 19), actor=specialist
        )
    assert str(refusal.value) == matter_services.DEADLINE_BEFORE_ENGAGEMENT


def test_the_correction_form_and_the_service_share_one_sentence():
    from app.matters import forms

    assert not hasattr(forms, "DEADLINE_BEFORE_ENGAGEMENT"), (
        "the sentence lives with the rule, in the service"
    )
    assert matter_services.DEADLINE_BEFORE_ENGAGEMENT == (
        "Tagasiside tähtaeg ei saa olla enne kaasamise kuupäeva."
    )


# ---------------------------------------------------------------------------
# 2. A recorded send is never in the future — in Tallinn
# ---------------------------------------------------------------------------


def _draft_with_evidence(matter, actor):
    submission = submission_services.create_submission(
        matter=matter, title="Koja arvamus", actor=actor
    )
    submission_services.attach_final_evidence(
        submission=submission,
        content=b"%PDF-1.4 synthetic final opinion",
        original_filename="arvamus.pdf",
        mime_type="application/pdf",
        actor=actor,
    )
    submission.refresh_from_db()
    return submission


@pytest.mark.parametrize(
    "sent_at",
    [
        _tallinn(TOMORROW),
        _tallinn(TODAY + datetime.timedelta(days=400)),
        # 22:30 UTC on the 24th is 01:30 on the 25th in Tallinn. The business
        # date is Tallinn's; a UTC reading would let this through.
        datetime.datetime(2026, 9, 24, 22, 30, tzinfo=datetime.UTC),
    ],
)
def test_mark_submission_sent_refuses_a_future_send(normal_matter, specialist, sent_at):
    submission = _draft_with_evidence(normal_matter, specialist)
    before = _events(ChangeEventType.SUBMISSION_SENT)

    with pytest.raises(DomainError) as refusal:
        submission_services.mark_submission_sent(
            submission=submission,
            actor=specialist,
            sent_at=sent_at,
            sent_at_precision=SentAtPrecision.DATE,
        )

    assert str(refusal.value) == submission_services.SENT_DATE_IN_THE_FUTURE
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.DRAFT
    assert submission.sent_at is None
    assert _events(ChangeEventType.SUBMISSION_SENT) == before


def test_a_send_late_today_in_tallinn_is_today(normal_matter, specialist):
    submission = _draft_with_evidence(normal_matter, specialist)

    submission_services.mark_submission_sent(
        submission=submission,
        actor=specialist,
        sent_at=_tallinn(TODAY, 23, 30),
        sent_at_precision=SentAtPrecision.TIMESTAMP,
    )

    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.SENT


def _register(matter, actor, organisation, capture_evidence, sent_at):
    version = capture_evidence(
        matter,
        b"%PDF-1.4 synthetic evidence",
        "koja-arvamus.pdf",
        "application/pdf",
        title="Koja arvamus eelnõule",
        role=DocumentRole.KODA_SUBMISSION_FINAL,
    )
    return submission_services.register_sent_opinion(
        document=version.document,
        version=version,
        title="Koja arvamus eelnõule",
        recipients=[organisation],
        sent_at=sent_at,
        sent_at_precision=SentAtPrecision.DATE,
        actor=actor,
    )


def test_register_sent_opinion_refuses_a_future_send_and_writes_nothing(
    normal_matter, specialist, organisation, capture_evidence
):
    created = _events(ChangeEventType.SUBMISSION_CREATED)
    sent = _events(ChangeEventType.SUBMISSION_SENT)

    with pytest.raises(DomainError) as refusal:
        _register(normal_matter, specialist, organisation, capture_evidence, _tallinn(TOMORROW))

    assert str(refusal.value) == submission_services.SENT_DATE_IN_THE_FUTURE
    assert not Submission.objects.filter(matter=normal_matter).exists()
    assert _events(ChangeEventType.SUBMISSION_CREATED) == created
    assert _events(ChangeEventType.SUBMISSION_SENT) == sent


def test_correct_sent_opinion_refuses_moving_a_send_into_the_future(
    normal_matter, specialist, organisation, capture_evidence
):
    submission = _register(
        normal_matter,
        specialist,
        organisation,
        capture_evidence,
        _tallinn(TODAY - datetime.timedelta(days=3)),
    )
    submission.refresh_from_db()
    stored = submission.sent_at
    before = _events(ChangeEventType.SUBMISSION_CORRECTED)

    with pytest.raises(DomainError) as refusal:
        submission_services.correct_sent_opinion(
            submission=submission,
            sent_at=_tallinn(TOMORROW),
            sent_at_precision=SentAtPrecision.DATE,
            summary="Parandatud kokkuvõte.",
            kind=submission.kind,
            actor=specialist,
        )

    assert str(refusal.value) == submission_services.SENT_DATE_IN_THE_FUTURE
    submission.refresh_from_db()
    assert submission.sent_at == stored
    assert submission.summary == ""
    assert _events(ChangeEventType.SUBMISSION_CORRECTED) == before


def test_a_correction_that_keeps_the_stored_date_is_not_a_new_send_fact(
    normal_matter, specialist, organisation, capture_evidence
):
    """An archival row already carrying a future day is reported by the
    preflight and never rewritten; correcting its summary states no new send
    date, so it is not refused for the one it already had."""
    submission = _register(
        normal_matter, specialist, organisation, capture_evidence, _tallinn(TODAY)
    )
    Submission.objects.filter(pk=submission.pk).update(sent_at=_tallinn(TOMORROW))
    submission.refresh_from_db()

    submission_services.correct_sent_opinion(
        submission=submission,
        sent_at=submission.sent_at,
        sent_at_precision=SentAtPrecision.DATE,
        summary="Kokkuvõte lisati hiljem.",
        kind=submission.kind,
        actor=specialist,
    )

    submission.refresh_from_db()
    assert submission.summary == "Kokkuvõte lisati hiljem."


def test_the_forms_print_the_services_sentence():
    assert submission_services.SENT_DATE_IN_THE_FUTURE == "Saatmise kuupäev ei saa olla tulevikus."


# ---------------------------------------------------------------------------
# 3. A NextAction's precision is one the product can render
# ---------------------------------------------------------------------------


def _set(matter, **kwargs):
    return workflow_services.set_next_action(matter=matter, text="Vaatan eelnõu üle", **kwargs)


@pytest.mark.parametrize(
    ("target_date", "precision"),
    [
        (datetime.date(2026, 10, 1), "BOGUS"),
        (datetime.date(2026, 10, 1), ""),
        # The undated step with a period: a period of nothing.
        (None, DatePrecision.QUARTER),
        (None, DatePrecision.MONTH),
        (None, DatePrecision.INFERRED),
    ],
)
def test_set_next_action_refuses_a_precision_it_cannot_mean(
    normal_matter, specialist, target_date, precision
):
    before = _events(ChangeEventType.NEXT_ACTION_SET)

    with pytest.raises(DomainError):
        _set(normal_matter, target_date=target_date, date_precision=precision, actor=specialist)

    assert not NextAction.objects.filter(matter=normal_matter).exists()
    assert _events(ChangeEventType.NEXT_ACTION_SET) == before


@pytest.mark.parametrize(
    ("target_date", "precision"),
    [
        # docs/adr/0106: a step may have no date yet, and absence is `EXACT`.
        (None, DatePrecision.EXACT),
        (datetime.date(2026, 10, 1), DatePrecision.MONTH),
        (datetime.date(2026, 10, 1), DatePrecision.QUARTER),
        (datetime.date(2026, 1, 1), DatePrecision.YEAR),
        # Historical, not offered for new input, still valid (docs/adr/0079 §7, §8).
        (datetime.date(2026, 7, 1), DatePrecision.HALF_YEAR),
        (datetime.date(2026, 10, 3), DatePrecision.INFERRED),
    ],
)
def test_every_supported_shape_is_still_accepted(normal_matter, specialist, target_date, precision):
    action = _set(
        normal_matter, target_date=target_date, date_precision=precision, actor=specialist
    )
    assert action.date_precision == precision
    assert action.target_date == target_date


def test_kind_and_date_meaning_were_already_the_services_own(normal_matter, specialist):
    """Revalidated rather than duplicated: these two refusals predate ENG-043."""
    with pytest.raises(DomainError):
        _set(normal_matter, kind="NOPE", actor=specialist)
    with pytest.raises(DomainError):
        _set(normal_matter, date_semantics="NOPE", actor=specialist)
    assert not NextAction.objects.filter(matter=normal_matter).exists()


def _waiting(matter, actor):
    return _set(
        matter,
        kind=ActionKind.WAIT,
        date_semantics=DateSemantics.EXPECTED_AROUND,
        target_date=datetime.date(2026, 9, 20),
        actor=actor,
    )


@pytest.mark.parametrize(
    ("next_review", "precision"),
    [
        (datetime.date(2026, 11, 1), "XX"),
        (None, DatePrecision.MONTH),
    ],
)
def test_acknowledge_review_refuses_a_precision_it_cannot_mean(
    normal_matter, specialist, next_review, precision
):
    """The verifier's case: `acknowledge_review` stored `'XX'`."""
    action = _waiting(normal_matter, specialist)
    before = _events(ChangeEventType.NEXT_ACTION_REVIEWED)

    with pytest.raises(DomainError):
        workflow_services.acknowledge_review(
            action=action, actor=specialist, next_review_date=next_review, date_precision=precision
        )

    action.refresh_from_db()
    assert action.target_date == datetime.date(2026, 9, 20)
    assert action.date_precision == DatePrecision.EXACT
    assert _events(ChangeEventType.NEXT_ACTION_REVIEWED) == before


def test_acknowledge_review_still_takes_a_period_or_no_date(normal_matter, specialist):
    action = _waiting(normal_matter, specialist)

    workflow_services.acknowledge_review(
        action=action,
        actor=specialist,
        next_review_date=datetime.date(2026, 11, 1),
        date_precision=DatePrecision.MONTH,
    )
    action.refresh_from_db()
    assert action.date_precision == DatePrecision.MONTH

    workflow_services.acknowledge_review(action=action, actor=specialist, next_review_date=None)
    action.refresh_from_db()
    assert action.target_date is None
    assert action.date_precision == DatePrecision.EXACT


# ---------------------------------------------------------------------------
# 4. The database says the same about the vocabularies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("date_precision", "BOGUS", "workflow_next_action_precision_vocabulary"),
        ("kind", "NOPE", "workflow_next_action_kind_vocabulary"),
        ("date_semantics", "NOPE", "workflow_next_action_date_semantics_vocabulary"),
        ("status", "XX", "workflow_next_action_status_vocabulary"),
    ],
)
def test_the_table_refuses_a_value_outside_its_vocabulary(field, value, constraint):
    action = factories.NextActionFactory(target_date=datetime.date(2026, 10, 1))

    with pytest.raises(IntegrityError) as refusal, transaction.atomic():
        NextAction.objects.filter(pk=action.pk).update(**{field: value})

    assert constraint in str(refusal.value)


def test_the_table_refuses_a_period_of_nothing_and_keeps_the_undated_step():
    """`target_date IS NOT NULL OR date_precision = 'EXACT'` — docs/adr/0106's
    undated step survives, and only its approximate twin is refused."""
    action = factories.NextActionFactory(target_date=None, date_precision=DatePrecision.EXACT)

    with pytest.raises(IntegrityError) as refusal, transaction.atomic():
        NextAction.objects.filter(pk=action.pk).update(date_precision=DatePrecision.QUARTER)
    assert "workflow_next_action_undated_is_exact" in str(refusal.value)

    NextAction.objects.filter(pk=action.pk).update(
        target_date=datetime.date(2026, 10, 1), date_precision=DatePrecision.QUARTER
    )
    action.refresh_from_db()
    assert action.date_precision == DatePrecision.QUARTER


def test_the_constraint_migration_is_labelled_as_one():
    """ENG-014: a migration that makes the database refuse rows it accepted is
    not additive, and `migration_plan` must say so before anybody runs it."""
    loader = MigrationLoader(None, ignore_no_migrations=True)
    name = next(
        key[1]
        for key in loader.disk_migrations
        if key[0] == "workflow" and key[1].startswith("0009_")
    )
    planned = deployment.consequential_operations(loader.get_migration("workflow", name))

    assert sorted(planned) == ["AddConstraint"]


# ---------------------------------------------------------------------------
# 5. The preflight that answers «would the migration fail» before it is run
# ---------------------------------------------------------------------------


def _preflight() -> tuple[int, str]:
    out = io.StringIO()
    try:
        call_command("check_domain_invariants", stdout=out)
    except SystemExit as exit_:
        return int(exit_.code or 0), out.getvalue()
    return 0, out.getvalue()


def _without_the_next_action_checks() -> None:
    """The state of a database the release has not migrated yet.

    PostgreSQL DDL is transactional, so the test's own rollback restores every
    constraint (the pattern `tests/test_authorization.py` uses).
    """
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        for name in (
            "workflow_next_action_precision_vocabulary",
            "workflow_next_action_kind_vocabulary",
            "workflow_next_action_date_semantics_vocabulary",
            "workflow_next_action_status_vocabulary",
            "workflow_next_action_undated_is_exact",
        ):
            cursor.execute(f"ALTER TABLE workflow_nextaction DROP CONSTRAINT {name}")


def test_a_clean_database_passes_the_preflight(normal_matter, specialist):
    _set(normal_matter, target_date=None, actor=specialist)
    _add(
        normal_matter,
        specialist,
        occurred_on=datetime.date(2025, 10, 1),
        occurred_on_precision=DatePrecision.MONTH,
        feedback_deadline=datetime.date(2025, 10, 15),
    )

    code, output = _preflight()

    assert code == 0, output
    assert "No invariant violations found." in output


def test_the_preflight_reports_every_row_the_migration_would_refuse(specialist):
    _without_the_next_action_checks()
    rows = {
        "next-action-precision": factories.NextActionFactory(
            target_date=datetime.date(2026, 10, 1)
        ),
        "next-action-undated-period": factories.NextActionFactory(target_date=None),
        "next-action-kind": factories.NextActionFactory(target_date=datetime.date(2026, 10, 1)),
        "next-action-date-semantics": factories.NextActionFactory(
            target_date=datetime.date(2026, 10, 1)
        ),
        "next-action-status": factories.NextActionFactory(target_date=datetime.date(2026, 10, 1)),
    }
    NextAction.objects.filter(pk=rows["next-action-precision"].pk).update(date_precision="BOGUS")
    NextAction.objects.filter(pk=rows["next-action-undated-period"].pk).update(
        date_precision=DatePrecision.QUARTER
    )
    NextAction.objects.filter(pk=rows["next-action-kind"].pk).update(kind="NOPE")
    NextAction.objects.filter(pk=rows["next-action-date-semantics"].pk).update(
        date_semantics="NOPE"
    )
    NextAction.objects.filter(pk=rows["next-action-status"].pk).update(status="XX")
    events = ChangeEvent.objects.count()
    stored = list(
        NextAction.objects.order_by("pk").values_list("pk", "kind", "status", "date_precision")
    )

    code, output = _preflight()

    assert code == 1
    for finding, row in rows.items():
        assert finding in output
        assert str(row.pk) in output
    assert "workflow.0009" in output
    # Read-only: nothing repaired, nothing audited.
    assert ChangeEvent.objects.count() == events
    assert (
        list(
            NextAction.objects.order_by("pk").values_list("pk", "kind", "status", "date_precision")
        )
        == stored
    )


def test_the_preflight_reports_the_service_invariants_too(
    normal_matter, specialist, organisation, capture_evidence
):
    """Rows no `CHECK` refuses but the services now would: they do not stop the
    migration, and they are said separately so nobody mistakes one for the other."""
    wrong = _add(normal_matter, specialist, occurred_on=datetime.date(2026, 9, 10))
    MatterEngagement.objects.filter(pk=wrong.pk).update(feedback_deadline=datetime.date(2026, 9, 9))
    # Inside its month: the period rule, not the anchor, decides.
    inside = _add(
        normal_matter,
        specialist,
        occurred_on=datetime.date(2025, 10, 1),
        occurred_on_precision=DatePrecision.MONTH,
    )
    MatterEngagement.objects.filter(pk=inside.pk).update(
        occurred_on=datetime.date(2025, 10, 20), feedback_deadline=datetime.date(2025, 10, 5)
    )
    future = _register(normal_matter, specialist, organisation, capture_evidence, _tallinn(TODAY))
    Submission.objects.filter(pk=future.pk).update(sent_at=_tallinn(TOMORROW))
    today = _register(normal_matter, specialist, organisation, capture_evidence, _tallinn(TODAY))
    Submission.objects.filter(pk=today.pk).update(sent_at=_tallinn(TODAY, 23, 59))

    code, output = _preflight()

    assert code == 1
    assert "engagement-deadline-before-round" in output
    assert str(wrong.pk) in output
    assert str(inside.pk) not in output
    assert "submission-sent-in-future" in output
    assert str(future.pk) in output
    assert str(today.pk) not in output
    assert "do not block" in output
    # Identifiers only: a title in this corpus can be the confidential part.
    assert "Kaasati liikmeid" not in output
    assert "Koja arvamus eelnõule" not in output
