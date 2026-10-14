"""Independent QA 2026-10-14 — workflow area: Koja ettepanek / pöördumine (ADR 0151, PR #427).

Path A (prepared, not sent) and path B (sent in the same save) must converge on
the same canonical facts — including what happens to an optional `Arvamuse
tähtaeg` once the proposal has gone out (0151 amendment of 2026-10-13 §4).
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters.dashboard import drafting_matters
from app.matters.enums import ResponseDeadlineOutcome
from app.matters.models import Matter, MatterResponseDeadline
from app.matters.response_deadlines import deadline_revision
from app.matters.work_items import response_obligation_of, response_obligations
from app.organisations.models import Organisation
from app.submissions.enums import SubmissionKind, SubmissionStatus
from app.submissions.models import Submission
from app.workflow.enums import Track
from app.workflow.models import NextAction, OpinionFollowUp
from tests.test_koda_proposal_create_flow import et, instrument, pdf, sent_fields

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")


def today() -> dt.date:
    return timezone.localdate()


@pytest.fixture
def ministry(db) -> Organisation:
    return Organisation.objects.create(name="QA Näidisministeerium")


def path_a(client, title, *, deadline=None) -> Matter:
    data = {"title": title, "legal_instruments": instrument("koja-ettepanek")}
    if deadline is not None:
        data["response_deadline"] = et(deadline)
    response = client.post(CREATE, data)
    assert response.status_code == 302
    return Matter.objects.get(title=title)


def send_later(client, matter, ministry, sent_on, **extra):
    data = {
        "upload": pdf("Pöördumine.pdf"),
        "recipients": [str(ministry.pk)],
        "sent_on": et(sent_on),
        **extra,
    }
    return client.post(reverse("matters:add_koda_opinion", kwargs={"pk": matter.pk}), data)


# ---------------------------------------------------------------------------
# QA-WORKFLOW-04 — path A + later send leaves the deadline outstanding; path B does not
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "QA-WORKFLOW-04: an initiative's optional Arvamuse tähtaeg stays outstanding "
        "(response_obligations, overdue once past) after the proposal is sent later through "
        "+ Koja arvamus unless «Vastab arvamuse küsimisele» is ticked; the fast path answers it"
    ),
)
def test_a_proposal_sent_later_does_not_leave_its_deadline_outstanding(
    signed_in, specialist, ministry, evidence_root
):
    due = today() + dt.timedelta(days=5)
    matter = path_a(signed_in, "QA hiljem saadetud pöördumine", deadline=due)
    assert response_obligation_of(matter, specialist).is_outstanding

    sent = send_later(signed_in, matter, ministry, today())
    assert sent.status_code == 200
    (proposal,) = Submission.objects.filter(matter=matter)
    assert proposal.kind == SubmissionKind.KODA_PROPOSAL
    assert proposal.status == SubmissionStatus.SENT

    matter.refresh_from_db()
    # Same as path B (tests/test_create_dates_and_related_matters.py
    # ::test_a_sent_proposal_with_a_deadline_leaves_nothing_outstanding).
    assert not response_obligation_of(matter, specialist).is_outstanding
    assert matter not in drafting_matters(specialist)


def test_path_a_later_send_evidence_deadline_still_outstanding(
    signed_in, specialist, ministry, evidence_root
):
    """Evidence for QA-WORKFLOW-04 (passes today): the two paths diverge.

    A deadline two days ago, the proposal sent today: path A still reports the
    obligation outstanding and overdue; path B (same answers) reports nothing."""
    due = today() - dt.timedelta(days=2)
    later = path_a(signed_in, "QA tee A", deadline=due)
    send_later(signed_in, later, ministry, today())
    later.refresh_from_db()

    signed_in.post(
        CREATE,
        sent_fields("QA tee B", ministry, response_deadline=et(due), files=[pdf("Pöördumine.pdf")]),
    )
    fast = Matter.objects.get(title="QA tee B")

    assert later.response_deadline == due
    assert later.response_requested_at is not None  # set by Uus teema for the initiative too
    assert response_obligation_of(later, specialist).is_outstanding
    assert response_obligation_of(later, specialist).is_overdue
    assert later in response_obligations(specialist)
    assert fast.response_deadline is None
    assert not response_obligation_of(fast, specialist).is_outstanding
    # The later send's panel offered only «Vastab arvamuse küsimisele» — a request wording.
    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": later.pk}))
    assert "Vastab arvamuse küsimisele" in page.content.decode()


def test_path_a_later_send_with_the_box_ticked_answers_the_deadline(
    signed_in, specialist, ministry, evidence_root
):
    due = today() + dt.timedelta(days=5)
    matter = path_a(signed_in, "QA tee A linnukesega", deadline=due)

    send_later(signed_in, matter, ministry, today(), vastab_tahtajale=deadline_revision(matter))

    matter.refresh_from_db()
    assert matter.response_deadline is None
    ended = MatterResponseDeadline.objects.get(matter=matter)
    assert ended.outcome == ResponseDeadlineOutcome.ANSWERED
    assert not response_obligation_of(matter, specialist).is_outstanding


# ---------------------------------------------------------------------------
# Path A — prepared, not sent: no invented facts; the deadline's life in the header
# ---------------------------------------------------------------------------


def test_path_a_invents_nothing_and_its_deadline_is_work(signed_in, specialist):
    due = today() + dt.timedelta(days=14)
    matter = path_a(signed_in, "QA ettevalmistatav pöördumine", deadline=due)

    assert matter.track == Track.KODA_INITIATIVE
    assert matter.received_date is None
    assert matter.response_deadline == due
    assert not Submission.objects.filter(matter=matter).exists()
    assert not OpinionFollowUp.objects.filter(submission__matter=matter).exists()
    assert not NextAction.objects.filter(matter=matter, follow_up__isnull=False).exists()
    # The optional deadline is outstanding work until the proposal goes out.
    assert response_obligation_of(matter, specialist).is_outstanding


def test_path_a_deadline_added_moved_and_cleared_in_the_header(signed_in, specialist):
    matter = path_a(signed_in, "QA tähtajata pöördumine")
    url = reverse("matters:response_deadline", kwargs={"pk": matter.pk})
    first = today() + dt.timedelta(days=10)
    second = today() + dt.timedelta(days=20)

    added = signed_in.post(
        url, {"tegevus": "muuda", "response_deadline": et(first), "revision": ""}
    )
    assert added.status_code == 200
    matter.refresh_from_db()
    assert matter.response_deadline == first
    assert response_obligation_of(matter, specialist).is_outstanding

    # «Uus arvamuse küsimine» is refused on an initiative.
    replaced = signed_in.post(
        url,
        {
            "tegevus": "muuda",
            "response_deadline": et(second),
            "change": "REPLACED",
            "previous_outcome": ResponseDeadlineOutcome.CANCELLED,
            "revision": deadline_revision(matter),
        },
    )
    assert replaced.status_code == 400
    matter.refresh_from_db()
    assert matter.response_deadline == first

    moved = signed_in.post(
        url,
        {
            "tegevus": "muuda",
            "response_deadline": et(second),
            "change": "MOVED",
            "revision": deadline_revision(matter),
        },
    )
    assert moved.status_code == 200
    matter.refresh_from_db()
    assert matter.response_deadline == second

    cleared = signed_in.post(
        url,
        {
            "tegevus": "muuda",
            "response_deadline": "",
            "previous_outcome": ResponseDeadlineOutcome.CANCELLED,
            "revision": deadline_revision(matter),
        },
    )
    assert cleared.status_code == 200
    matter.refresh_from_db()
    assert matter.response_deadline is None
    assert not response_obligation_of(matter, specialist).is_outstanding
    outcomes = sorted(
        MatterResponseDeadline.objects.filter(matter=matter).values_list("outcome", flat=True)
    )
    assert outcomes == sorted([ResponseDeadlineOutcome.MOVED, ResponseDeadlineOutcome.CANCELLED])


def test_path_b_with_a_deadline_answers_it_with_exactly_its_submission(
    signed_in, specialist, ministry, evidence_root
):
    due = today() + dt.timedelta(days=3)
    sent_on = today() - dt.timedelta(days=1)
    response = signed_in.post(
        CREATE,
        sent_fields(
            "QA kohe saadetud",
            ministry,
            sent_on=et(sent_on),
            response_deadline=et(due),
            files=[pdf("A.pdf"), pdf("B.pdf", "Lisa")],
        ),
    )
    assert response.status_code == 302
    matter = Matter.objects.get(title="QA kohe saadetud")
    (proposal,) = Submission.objects.filter(matter=matter)
    ended = MatterResponseDeadline.objects.get(matter=matter)
    assert (ended.outcome, ended.submission_id) == (ResponseDeadlineOutcome.ANSWERED, proposal.pk)
    (follow_up,) = OpinionFollowUp.objects.filter(submission=proposal)
    assert follow_up.first_due_on == sent_on + dt.timedelta(days=30)
    # No phantom incoming request: nothing arrived.
    assert matter.received_date is None
    assert matter.response_deadline is None
    # Exactly one check is live, and it is current work (0151 amendment §7).
    live = NextAction.objects.filter(matter=matter, status__in=("OPEN", "PLANNED"))
    assert [row.follow_up_id for row in live] == [follow_up.pk]
