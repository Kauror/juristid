"""`Arvamuse tähtaeg` keeps its history and is ended only by an answer that names it.

Historical regression of 2026-10-04, F-005 and F-006:

* a replaced deadline vanished from every surface — only an audit row or a
  `Märge` somebody remembered to write kept it;
* an answered deadline kept reading as owed, in the warning colour, even on a
  closed and enacted file — and any opinion, sent for any earlier request,
  discharged a later one.

What is protected here:

* the current deadline lives on `Matter.response_deadline`, everything that
  stopped being current on `MatterResponseDeadline`, never both;
* moving a deadline is not replacing it, and replacing one needs an outcome
  the person chose for the old request;
* an opinion sent for an earlier request does not answer a later one; an
  answer names its opinion or says in words where it was given, and creates no
  `Submission`;
* emptying the field keeps the history; closing is not answering; reopening
  carries a deadline forward only when asked to;
* a deadline from before requests were tracked keeps its old reading;
* a withdrawn answering opinion leaves the answer needing review;
* the linked opinion is named only to a reader who may see it.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from app.core.enums import Visibility
from app.core.errors import DomainError
from app.matters import work_items as wi
from app.matters.enums import ResponseDeadlineChange, ResponseDeadlineOutcome
from app.matters.models import MatterResponseDeadline
from app.matters.response_deadlines import (
    ANSWER_NEEDS_A_BASIS,
    CHANGE_NEEDS_A_MEANING,
    FOREIGN_SUBMISSION,
    REPLACED_NEEDS_AN_OUTCOME,
    STALE_DEADLINE_REFUSAL,
    change_response_deadline,
    deadline_revision,
    ended_deadlines,
    resolve_response_deadline,
)
from app.matters.selectors import response_deadline_of
from app.matters.services import close_matter, reopen_matter_into_stage
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.submissions.services import withdraw_submission
from app.workflow.enums import Disposition
from app.workflow.models import StageVocabulary
from tests import factories

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate


def _days(n: int) -> dt.date:
    return TODAY() + dt.timedelta(days=n)


@pytest.fixture
def send(capture_evidence):
    """A sent `Koja arvamus` on ``matter``, optionally restricted below it."""

    def _send(matter, *, title="Koja arvamus", restricted=False):
        extra = {"visibility_override": Visibility.RESTRICTED} if restricted else {}
        version = capture_evidence(
            matter, b"%PDF-1.4 synthetic opinion", f"{title}.pdf", "application/pdf", **extra
        )
        return factories.SubmissionFactory(
            matter=matter,
            title=title,
            status=SubmissionStatus.SENT,
            sent_at=timezone.now(),
            final_version=version,
            **extra,
        )

    return _send


def _requested(owner, deadline):
    """A Matter whose deadline was recorded as a request, the way the doors record it."""
    matter = factories.MatterFactory(owner=owner)
    change_response_deadline(matter=matter, deadline=deadline, actor=owner)
    matter.refresh_from_db()
    return matter


def _owed(user):
    return set(wi.response_obligations(user).values_list("pk", flat=True))


# ---------------------------------------------------------------------------
# A — set, move, replace
# ---------------------------------------------------------------------------


def test_a_deadline_set_where_there_was_none_is_a_request_recorded_now(specialist):
    matter = _requested(specialist, _days(10))

    assert matter.response_deadline == _days(10)
    assert matter.response_requested_at is not None
    assert not MatterResponseDeadline.objects.filter(matter=matter).exists()


def test_uus_teema_records_its_deadline_as_a_request(signed_in, specialist):
    from app.matters.models import Matter

    response = signed_in.post(
        reverse("matters:matter_create"),
        {"title": "Uus päring (sünteetiline)", "response_deadline": _days(20).strftime("%d.%m.%Y")},
    )

    assert response.status_code in (200, 302)
    matter = Matter.objects.get(title="Uus päring (sünteetiline)")
    assert matter.response_deadline == _days(20)
    assert matter.response_requested_at is not None


def test_moving_keeps_the_request_and_the_old_date(specialist):
    matter = _requested(specialist, _days(5))
    requested = matter.response_requested_at

    change_response_deadline(
        matter=matter, deadline=_days(15), actor=specialist, change=ResponseDeadlineChange.MOVED
    )
    matter.refresh_from_db()

    assert matter.response_deadline == _days(15)
    assert matter.response_requested_at == requested
    (moved,) = MatterResponseDeadline.objects.filter(matter=matter)
    assert moved.outcome == ResponseDeadlineOutcome.MOVED
    assert (moved.deadline, moved.next_deadline) == (_days(5), _days(15))
    assert matter.pk in _owed(specialist)


def test_a_changed_date_must_say_what_it_means(specialist):
    matter = _requested(specialist, _days(5))

    with pytest.raises(DomainError) as refusal:
        change_response_deadline(matter=matter, deadline=_days(15), actor=specialist)

    assert str(refusal.value) == CHANGE_NEEDS_A_MEANING
    matter.refresh_from_db()
    assert matter.response_deadline == _days(5)


def test_a_new_request_needs_an_outcome_for_the_old_one(specialist):
    matter = _requested(specialist, _days(5))

    with pytest.raises(DomainError) as refusal:
        change_response_deadline(
            matter=matter,
            deadline=_days(40),
            actor=specialist,
            change=ResponseDeadlineChange.REPLACED,
        )

    assert str(refusal.value) == REPLACED_NEEDS_AN_OUTCOME
    assert not MatterResponseDeadline.objects.filter(matter=matter).exists()


def test_an_unanswered_request_replaced_reads_as_replaced_not_answered(specialist):
    matter = _requested(specialist, _days(5))

    change_response_deadline(
        matter=matter,
        deadline=_days(40),
        actor=specialist,
        change=ResponseDeadlineChange.REPLACED,
        previous_outcome=ResponseDeadlineOutcome.SUPERSEDED,
    )

    (ended,) = ended_deadlines(matter, specialist)
    assert ended.outcome == ResponseDeadlineOutcome.SUPERSEDED
    assert ended.next_display == f"{_days(40).day}.{_days(40).month}.{_days(40).year}"


# ---------------------------------------------------------------------------
# B — an old answer does not answer a new request
# ---------------------------------------------------------------------------


def test_an_opinion_sent_for_the_old_request_does_not_answer_the_new_one(specialist, send):
    matter = _requested(specialist, _days(-30))
    opinion = send(matter)
    change_response_deadline(
        matter=matter,
        deadline=_days(20),
        actor=specialist,
        change=ResponseDeadlineChange.REPLACED,
        previous_outcome=ResponseDeadlineOutcome.ANSWERED,
        previous_submission=opinion,
    )
    matter.refresh_from_db()

    # The new request is owed, although a sent opinion exists on the file.
    assert matter.pk in _owed(specialist)
    (old,) = ended_deadlines(matter, specialist)
    assert old.outcome == ResponseDeadlineOutcome.ANSWERED
    assert old.submission == opinion


def test_a_recorded_request_is_not_discharged_by_any_opinion_until_answered(specialist, send):
    matter = _requested(specialist, _days(-2))
    opinion = send(matter)

    assert matter.pk in _owed(specialist)
    header = response_deadline_of(matter, specialist)
    assert header.is_overdue and not header.settled

    resolve_response_deadline(
        matter=matter,
        outcome=ResponseDeadlineOutcome.ANSWERED,
        actor=specialist,
        submission=opinion,
    )
    matter.refresh_from_db()

    assert matter.response_deadline is None
    assert matter.pk not in _owed(specialist)
    assert response_deadline_of(matter, specialist) is None


@pytest.mark.parametrize("days", [10, -10], ids=["before-the-deadline", "after-the-deadline"])
def test_answering_before_or_after_the_deadline_both_end_it(specialist, send, days):
    matter = _requested(specialist, _days(days))
    opinion = send(matter)

    resolve_response_deadline(
        matter=matter,
        outcome=ResponseDeadlineOutcome.ANSWERED,
        actor=specialist,
        submission=opinion,
        expected_revision=deadline_revision(matter),
    )

    (ended,) = ended_deadlines(matter, specialist)
    assert ended.deadline == _days(days)
    assert ended.outcome == ResponseDeadlineOutcome.ANSWERED


# ---------------------------------------------------------------------------
# C — an answer without a sent opinion; clearing; stale tabs
# ---------------------------------------------------------------------------


def test_an_answer_given_elsewhere_needs_words_and_creates_no_opinion(specialist):
    matter = _requested(specialist, _days(3))
    before = Submission.objects.count()

    with pytest.raises(DomainError) as refusal:
        resolve_response_deadline(
            matter=matter, outcome=ResponseDeadlineOutcome.ANSWERED, actor=specialist
        )
    assert str(refusal.value) == ANSWER_NEEDS_A_BASIS

    resolve_response_deadline(
        matter=matter,
        outcome=ResponseDeadlineOutcome.ANSWERED,
        actor=specialist,
        note="Vastati e-kirjaga ministeeriumile 2.10.2026 (sünteetiline).",
    )

    assert Submission.objects.count() == before
    assert not Submission.objects.filter(matter=matter, status=SubmissionStatus.SENT).exists()
    (ended,) = ended_deadlines(matter, specialist)
    assert ended.note.startswith("Vastati e-kirjaga")


def test_emptying_the_field_keeps_the_deadline_as_withdrawn(specialist):
    matter = _requested(specialist, _days(3))

    change_response_deadline(matter=matter, deadline=None, actor=specialist)
    matter.refresh_from_db()

    assert matter.response_deadline is None
    assert matter.response_requested_at is None
    (ended,) = ended_deadlines(matter, specialist)
    assert ended.outcome == ResponseDeadlineOutcome.CANCELLED
    assert ended.deadline == _days(3)


def test_a_stale_tab_ends_nothing(specialist):
    matter = _requested(specialist, _days(3))
    stale = deadline_revision(matter)
    change_response_deadline(
        matter=matter, deadline=_days(9), actor=specialist, change=ResponseDeadlineChange.MOVED
    )

    with pytest.raises(DomainError) as refusal:
        resolve_response_deadline(
            matter=matter,
            outcome=ResponseDeadlineOutcome.NOT_ANSWERING,
            actor=specialist,
            expected_revision=stale,
        )

    assert str(refusal.value) == STALE_DEADLINE_REFUSAL
    matter.refresh_from_db()
    assert matter.response_deadline == _days(9)


def test_another_files_opinion_is_never_an_answer(specialist, send):
    matter = _requested(specialist, _days(3))
    elsewhere = send(factories.MatterFactory(owner=specialist))

    with pytest.raises(DomainError, match=FOREIGN_SUBMISSION):
        resolve_response_deadline(
            matter=matter,
            outcome=ResponseDeadlineOutcome.ANSWERED,
            actor=specialist,
            submission=elsewhere,
        )
    assert not MatterResponseDeadline.objects.filter(matter=matter).exists()


# ---------------------------------------------------------------------------
# D — closure, reopening, the legacy reading
# ---------------------------------------------------------------------------


def test_a_closed_files_deadline_is_settled_and_not_answered(specialist):
    matter = _requested(specialist, _days(-5))
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)
    matter.refresh_from_db()

    header = response_deadline_of(matter, specialist)

    assert header.settled == "teema suletud"
    assert not header.is_overdue
    assert matter.pk not in _owed(specialist)
    assert not MatterResponseDeadline.objects.filter(
        matter=matter, outcome=ResponseDeadlineOutcome.ANSWERED
    ).exists()


@pytest.mark.parametrize("keep", [False, True], ids=["left-behind", "carried-forward"])
def test_reopening_carries_the_deadline_forward_only_when_asked(specialist, keep):
    matter = _requested(specialist, _days(-5))
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)

    reopen_matter_into_stage(
        matter=matter,
        stage=StageVocabulary.objects.get(key="consultation"),
        actor=specialist,
        keep_response_deadline=keep,
    )
    matter.refresh_from_db()

    if keep:
        assert matter.response_deadline == _days(-5)
        assert matter.pk in _owed(specialist)
    else:
        assert matter.response_deadline is None
        (ended,) = ended_deadlines(matter, specialist)
        assert ended.outcome == ResponseDeadlineOutcome.CLOSED


def test_a_deadline_from_before_requests_were_tracked_keeps_its_old_reading(specialist, send):
    matter = factories.MatterFactory(owner=specialist, response_deadline=_days(-5))
    assert matter.response_requested_at is None
    assert matter.pk in _owed(specialist)

    send(matter)

    assert matter.pk not in _owed(specialist)
    header = response_deadline_of(matter, specialist)
    assert header.settled == "lõpetatud"
    assert not header.is_overdue


# ---------------------------------------------------------------------------
# E — a withdrawn answer; what a reader may see
# ---------------------------------------------------------------------------


def test_a_withdrawn_answer_needs_review_and_reopens_nothing(specialist, send):
    matter = _requested(specialist, _days(-1))
    opinion = send(matter)
    resolve_response_deadline(
        matter=matter,
        outcome=ResponseDeadlineOutcome.ANSWERED,
        actor=specialist,
        submission=opinion,
    )

    withdraw_submission(submission=opinion, reason="Sünteetiline tagasivõtmine", actor=specialist)

    (ended,) = ended_deadlines(matter, specialist)
    assert ended.needs_review
    matter.refresh_from_db()
    assert matter.response_deadline is None


def test_the_answering_opinion_is_named_only_to_a_reader_who_may_see_it(specialist, reader, send):
    matter = _requested(specialist, _days(-1))
    secret = send(matter, title="Salajane arvamus", restricted=True)
    resolve_response_deadline(
        matter=matter, outcome=ResponseDeadlineOutcome.ANSWERED, actor=specialist, submission=secret
    )

    (seen_by_reader,) = ended_deadlines(matter, reader)
    assert seen_by_reader.submission is None
    assert not seen_by_reader.needs_review
    (seen_by_owner,) = ended_deadlines(matter, specialist)
    assert seen_by_owner.submission == secret


# ---------------------------------------------------------------------------
# F — the page
# ---------------------------------------------------------------------------


def test_the_header_and_rail_show_the_ended_deadline(signed_in, specialist, send):
    matter = _requested(specialist, _days(-3))
    opinion = send(matter)
    response = signed_in.post(
        reverse("matters:response_deadline", kwargs={"pk": matter.pk}),
        {
            "tegevus": "lopeta",
            "revision": deadline_revision(matter),
            "previous_outcome": ResponseDeadlineOutcome.ANSWERED,
            "previous_submission": str(opinion.pk),
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}))
    body = page.content.decode()
    day = _days(-3)
    assert f"{day.day}.{day.month}.{day.year} · vastatud" in body
    assert 'id="arvamuse-tahtajad"' in body


def test_the_header_refuses_a_changed_date_with_no_meaning(signed_in, specialist):
    matter = _requested(specialist, _days(3))

    response = signed_in.post(
        reverse("matters:response_deadline", kwargs={"pk": matter.pk}),
        {
            "tegevus": "muuda",
            "revision": deadline_revision(matter),
            "response_deadline": _days(30).strftime("%d.%m.%Y"),
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 400
    assert CHANGE_NEEDS_A_MEANING in response.content.decode()
    matter.refresh_from_db()
    assert matter.response_deadline == _days(3)


def test_the_register_draws_a_settled_deadline_without_the_warning(signed_in, specialist):
    matter = _requested(specialist, _days(-5))
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)

    body = signed_in.get(reverse("matters:matter_list") + "?olek=koik").content.decode()

    day = _days(-5)
    assert f"{day.day}.{day.month}.{day.year} · teema suletud" in body
    row = body[body.index(f"{day.day}.{day.month}.{day.year} · teema suletud") - 200 :]
    assert "dateline--deadline" not in row[:220]
