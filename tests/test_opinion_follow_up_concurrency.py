"""Concurrent sends of one opinion leave exactly one check (docs/adr/0146 §3).

Real transactions and real threads, because the guarantee is the database's:
two people pressing `Märgi saadetuks` on the same draft at once take turns on
the Matter's and the submission's row locks; one sends and schedules, the other
is refused, and the one-to-one `OpinionFollowUp.submission` would refuse a
second follow-up even if the locks did not.
"""

from __future__ import annotations

import threading

import pytest
from django.db import connections

from app.core.errors import DomainError
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.submissions.services import (
    attach_final_evidence,
    create_submission,
    mark_submission_sent_on_open_matter,
)
from app.workflow.enums import ActionStatus
from app.workflow.models import NextAction, OpinionFollowUp
from tests import factories
from tests.follow_ups import PDF

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def test_two_concurrent_sends_of_one_draft_schedule_one_check(specialist, other_specialist):
    ministry = factories.OrganisationFactory(name="Sünteetiline ministeerium")
    matter = factories.MatterFactory(owner=specialist)
    draft = create_submission(
        matter=matter, title="Arvamus", actor=specialist, recipients=[ministry]
    )
    attach_final_evidence(
        submission=draft,
        content=PDF,
        original_filename="arvamus.pdf",
        mime_type="application/pdf",
        actor=specialist,
    )
    start = threading.Barrier(2)
    outcomes: list[str] = []

    def send(actor) -> None:
        try:
            start.wait(timeout=10)
            mark_submission_sent_on_open_matter(
                submission=Submission.objects.get(pk=draft.pk), actor=actor
            )
            outcomes.append("sent")
        except DomainError as refusal:
            outcomes.append(str(refusal))
        finally:
            connections.close_all()

    threads = [threading.Thread(target=send, args=(who,)) for who in (specialist, other_specialist)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert sorted(outcomes) == ["Arvamus on juba saadetud.", "sent"]
    draft.refresh_from_db()
    assert draft.status == SubmissionStatus.SENT
    assert OpinionFollowUp.objects.filter(submission=draft).count() == 1
    assert (
        NextAction.objects.filter(
            follow_up__submission=draft, status__in=(ActionStatus.OPEN, ActionStatus.PLANNED)
        ).count()
        == 1
    )
