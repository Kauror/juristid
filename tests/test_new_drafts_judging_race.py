"""A dismissal committed while the sweep's verdict waits for the row stands (E1, post-r91).

Two real transactions, as in tests/test_closure_confirmation_race.py. A
(the head) dismisses a candidate and holds the row; B (the nightly sweep) read
the candidate as waiting before A committed, asked the model, and now wants to
write the verdict. B queues behind A's lock. When A commits, the candidate is
settled and B writes nothing on it: no strength, reason or Teema appears on a
candidate a person has already decided. No model is called.
"""

from __future__ import annotations

import threading

import pytest
from django.db import transaction
from django.test import override_settings

from app.register_watch import candidates, candidates_ai
from app.register_watch.models import CandidateStatus, Strength, TopicCandidate
from tests import factories
from tests.test_concurrency import LOCK_WAIT_TIMEOUT, run_in_thread, wait_for_a_blocked_backend
from tests.test_new_topics import FakeClient, _candidate

pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


@override_settings(REGISTER_SWEEP_AI=True)
def test_a_dismissal_committed_while_the_verdict_waits_is_not_overwritten():
    head = factories.UserFactory()
    teema = factories.MatterFactory(title="Äriühingute digitaliseerimine")
    candidate = _candidate()
    holder_ready = threading.Event()
    sweep_started = threading.Event()
    failures: list[BaseException] = []
    judged: list[int] = []

    def dismiss_and_hold() -> None:
        try:
            with transaction.atomic():
                candidates.dismiss(candidate, actor=head)
                holder_ready.set()
                sweep_started.wait(timeout=LOCK_WAIT_TIMEOUT)
                wait_for_a_blocked_backend()
        except BaseException as exc:
            failures.append(exc)

    def judge() -> None:
        try:
            holder_ready.wait(timeout=LOCK_WAIT_TIMEOUT)
            # The dismissal is not committed yet: the sweep still reads the
            # candidate as waiting and sends it to the model.
            client = FakeClient("STRONG", related=str(teema.pk))
            original = client._create

            def answer(**request):
                sweep_started.set()
                return original(**request)

            client.beta.messages.create = answer
            judged.append(candidates_ai.judge_unjudged(candidates_ai.Usage(), client=client))
            assert len(client.sent) == 1
        except BaseException as exc:
            failures.append(exc)

    threads = [run_in_thread(dismiss_and_hold), run_in_thread(judge)]
    for thread in threads:
        thread.join(timeout=40)

    assert failures == [], failures
    assert judged == [0]
    settled = TopicCandidate.objects.get(pk=candidate.pk)
    assert settled.status == CandidateStatus.DISMISSED
    assert settled.decided_by == head
    assert settled.strength == Strength.UNJUDGED
    assert settled.reason == ""
    assert settled.related_matter is None
