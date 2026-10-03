"""A terminal transition is decided on the current row, not on the caller's copy.

docs/adr/0110 made this the rule for `NextAction`: a person holding a page — two
presses of one button, or the same record open in two tabs — carries an instance
read before somebody else's write. Checking its status passes, the `UPDATE`
waits for the other transaction and then succeeds, and the audit trail gains a
second row for one act.

The same shape sat in the opinion withdrawal and in the intelligence facts'
cancel, confirm and reject. Each test here transitions a record and then calls
again with a copy fetched *before* that, which is exactly what the second tab
holds.
"""

from __future__ import annotations

import pytest

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.intelligence.enums import WorkVictoryStatus
from app.intelligence.models import MatterEffectiveDate, MatterImportantDate, MatterWorkVictory
from app.intelligence.services import (
    add_work_victory_candidate,
    cancel_effective_date,
    cancel_important_date,
    confirm_work_victory,
    reject_work_victory,
)
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.submissions.services import withdraw_submission
from app.workflow.dates import year_bounds
from app.workflow.enums import DatePrecision
from tests import factories
from tests.refusals import refused
from tests.test_dokumendid_opinion_block_retired import _sent_opinion

pytestmark = pytest.mark.django_db


def _count(event_type: str) -> int:
    return ChangeEvent.objects.filter(event_type=event_type).count()


def test_a_second_withdrawal_from_a_stale_copy_is_refused(
    normal_matter, specialist, organisation, evidence_root
):
    submission = _sent_opinion(normal_matter, actor=specialist, organisation=organisation)
    stale = Submission.objects.get(pk=submission.pk)

    withdraw_submission(submission=submission, actor=specialist)
    with refused("Tagasi võtta saab ainult saadetud arvamust."):
        withdraw_submission(submission=stale, actor=specialist)

    assert Submission.objects.get(pk=submission.pk).status == SubmissionStatus.WITHDRAWN
    assert _count(ChangeEventType.SUBMISSION_WITHDRAWN) == 1


def test_a_second_cancellation_of_a_milestone_is_refused(normal_matter, specialist):
    record = factories.ImportantDateFactory(matter=normal_matter)
    stale = MatterImportantDate.objects.get(pk=record.pk)

    cancel_important_date(record=record, actor=specialist)
    with refused("Ainult kehtivat tähtaega saab tühistada."):
        cancel_important_date(record=stale, actor=specialist)

    assert _count(ChangeEventType.IMPORTANT_DATE_CANCELLED) == 1


def test_a_second_cancellation_of_an_effective_date_is_refused(normal_matter, specialist):
    record = factories.EffectiveDateFactory(matter=normal_matter)
    stale = MatterEffectiveDate.objects.get(pk=record.pk)

    cancel_effective_date(record=record, actor=specialist)
    with refused("Ainult kehtivat jõustumist saab tühistada."):
        cancel_effective_date(record=stale, actor=specialist)

    assert _count(ChangeEventType.EFFECTIVE_DATE_CANCELLED) == 1


def test_a_decision_from_a_stale_tab_records_what_it_actually_overturned(
    normal_matter, specialist, department_head
):
    """Confirm in one tab, reject in another that still shows a candidate.

    Rejecting a confirmed victory is allowed, so the second act is not refused —
    but its audit row must say it overturned a confirmation, not a candidate.
    """
    start, end = year_bounds(2026)
    record = add_work_victory_candidate(
        matter=normal_matter,
        title="Ettepanek arvestati",
        actor=specialist,
        period_date=start,
        period_end=end,
        date_precision=DatePrecision.YEAR,
    )
    stale = MatterWorkVictory.objects.get(pk=record.pk)

    confirm_work_victory(record=record, actor=department_head)
    reject_work_victory(record=stale, actor=department_head, reason="Ei realiseerunud")

    rejection = ChangeEvent.objects.get(event_type=ChangeEventType.WORK_VICTORY_REJECTED)
    assert rejection.payload["from_status"] == WorkVictoryStatus.CONFIRMED.value
    with refused("Töövõit on juba märgitud mitterealiseerunuks."):
        reject_work_victory(record=stale, actor=department_head)
