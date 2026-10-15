"""QA-WORKFLOW-04 — a Chamber initiative's own `Arvamuse tähtaeg` is met by its send.

On a `Koja algatus` Teema the optional deadline is the day the Chamber's own
proposal is due by; nobody asked Koda for anything (docs/adr/0151, amendment of
2026-10-13). Every interactive door that records the proposal going out —
`Uus teema` «Märgi saadetud Koja arvamuseks», a later `+ Koja arvamus`,
Dokumendid «Registreeri saatmine», «Märgi saadetuks» — must leave the same
state: the deadline ended `ANSWERED`, linked to exactly that `Submission`, and
nothing outstanding. An ordinary Teema's deadline is a request and is still
answered only by an opinion a person names for it.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from app.matters.enums import ResponseDeadlineOutcome
from app.matters.models import Matter, MatterResponseDeadline
from app.matters.response_deadlines import change_response_deadline, deadline_revision
from app.matters.work_items import response_obligation_of, response_obligations
from app.organisations.models import Organisation
from app.submissions.enums import SubmissionKind, SubmissionStatus
from app.submissions.models import Submission
from app.workflow.enums import Track
from app.workflow.models import OpinionFollowUp
from tests.follow_ups import mark_sent, register_sent, send_koja_arvamus
from tests.test_koda_proposal_create_flow import et, instrument, pdf

pytestmark = pytest.mark.django_db

CREATE = reverse("matters:matter_create")


def today() -> dt.date:
    return timezone.localdate()


@pytest.fixture
def ministry(db) -> Organisation:
    return Organisation.objects.create(name="Näidisministeerium (algatus)")


def _initiative(client, title: str, *, deadline: dt.date | None) -> Matter:
    data = {"title": title, "legal_instruments": instrument("koja-ettepanek")}
    if deadline is not None:
        data["response_deadline"] = et(deadline)
    assert client.post(CREATE, data).status_code == 302
    matter = Matter.objects.get(title=title)
    assert matter.track == Track.KODA_INITIATIVE
    return matter


def _answered_by(matter: Matter) -> list[tuple[str, object, dt.date]]:
    return [
        (row.outcome, row.submission_id, row.deadline)
        for row in MatterResponseDeadline.objects.filter(matter=matter).order_by("ended_at")
    ]


def _assert_met_by(matter: Matter, sent: Submission, due: dt.date, viewer) -> None:
    matter.refresh_from_db()
    assert matter.response_deadline is None
    assert matter.response_requested_at is None
    assert _answered_by(matter) == [(ResponseDeadlineOutcome.ANSWERED, sent.pk, due)]
    assert not response_obligation_of(matter, viewer).is_outstanding
    assert matter not in response_obligations(viewer)


@pytest.mark.parametrize("door", ["registreeri", "margi_saadetuks", "koja_arvamus_service"])
def test_every_interactive_send_on_an_initiative_answers_its_own_deadline(
    door, signed_in, specialist, ministry, evidence_root
):
    due = today() - dt.timedelta(days=2)  # already past: it would read overdue
    matter = _initiative(signed_in, f"Algatus {door}", deadline=due)
    assert response_obligation_of(matter, specialist).is_overdue

    if door == "registreeri":
        sent = register_sent(matter, specialist, [ministry], today())
    elif door == "margi_saadetuks":
        sent = mark_sent(matter, specialist, [ministry])
    else:
        sent = send_koja_arvamus(matter, specialist, [ministry], today())

    assert sent.status == SubmissionStatus.SENT
    _assert_met_by(matter, sent, due, specialist)
    # The thirty-day check is scheduled exactly as before.
    assert OpinionFollowUp.objects.filter(submission=sent).count() == 1


def test_the_later_send_panel_says_the_send_meets_the_deadline_and_asks_nothing(
    signed_in, specialist, ministry
):
    due = today() + dt.timedelta(days=7)
    matter = _initiative(signed_in, "Algatus paneel", deadline=due)

    body = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content
    page = body.decode()
    assert "Saatmine täidab ettepaneku tähtaja" in page
    # The request wording, and the box that posts it, are for an ordinary Teema.
    assert "Vastab arvamuse küsimisele" not in page
    assert 'name="vastab_tahtajale"' not in page


def test_an_ordinary_teema_still_needs_its_deadline_named(
    signed_in, specialist, ministry, evidence_root
):
    """Not over-broad: a request is answered only by an opinion a person names."""
    title = "Tavaline teema tähtajaga"
    due = today() + dt.timedelta(days=5)
    assert signed_in.post(CREATE, {"title": title, "response_deadline": et(due)}).status_code == 302
    matter = Matter.objects.get(title=title)
    assert matter.track != Track.KODA_INITIATIVE

    sent = send_koja_arvamus(matter, specialist, [ministry], today())

    matter.refresh_from_db()
    assert sent.kind == SubmissionKind.FORMAL_OPINION
    assert matter.response_deadline == due
    assert not MatterResponseDeadline.objects.filter(matter=matter).exists()
    assert response_obligation_of(matter, specialist).is_outstanding
    page = signed_in.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content
    assert "Vastab arvamuse küsimisele" in page.decode()


def test_a_second_proposal_answers_a_deadline_set_after_the_first(
    signed_in, specialist, ministry, evidence_root
):
    first_due = today() + dt.timedelta(days=3)
    matter = _initiative(signed_in, "Algatus kaks ettepanekut", deadline=first_due)
    first = send_koja_arvamus(matter, specialist, [ministry], today() - dt.timedelta(days=1))

    # A follow-up proposal is due later; the lawyer sets the initiative's own
    # deadline again in the header.
    matter.refresh_from_db()
    second_due = today() + dt.timedelta(days=20)
    change_response_deadline(matter=matter, deadline=second_due, actor=specialist)
    assert response_obligation_of(matter, specialist).is_outstanding

    second = send_koja_arvamus(matter, specialist, [ministry], today())

    matter.refresh_from_db()
    assert matter.response_deadline is None
    assert _answered_by(matter) == [
        (ResponseDeadlineOutcome.ANSWERED, first.pk, first_due),
        (ResponseDeadlineOutcome.ANSWERED, second.pk, second_due),
    ]
    # A third send with no deadline current answers nothing and invents nothing.
    third = send_koja_arvamus(matter, specialist, [ministry], today())
    assert third.status == SubmissionStatus.SENT
    assert MatterResponseDeadline.objects.filter(matter=matter).count() == 2


def test_a_repeated_press_of_the_same_form_is_one_send_and_one_answer(
    signed_in, specialist, ministry, evidence_root
):
    due = today() + dt.timedelta(days=4)
    matter = _initiative(signed_in, "Algatus topeltvajutus", deadline=due)
    url = reverse("matters:add_koda_opinion", kwargs={"pk": matter.pk})
    once = str(uuid.uuid4())

    def press():
        return signed_in.post(
            url,
            {
                "upload": pdf("Ettepanek.pdf"),
                "recipients": [str(ministry.pk)],
                "sent_on": et(today()),
                "salvestus": once,
            },
        )

    assert press().status_code == 200
    press()

    (sent,) = Submission.objects.filter(matter=matter)
    _assert_met_by(matter, sent, due, specialist)


def test_the_box_ticked_by_a_stale_form_still_answers_once(
    signed_in, specialist, ministry, evidence_root
):
    """A form drawn before this change posts `vastab_tahtajale`; one answer, not two."""
    due = today() + dt.timedelta(days=4)
    matter = _initiative(signed_in, "Algatus vana vorm", deadline=due)
    response = signed_in.post(
        reverse("matters:add_koda_opinion", kwargs={"pk": matter.pk}),
        {
            "upload": pdf("Ettepanek.pdf"),
            "recipients": [str(ministry.pk)],
            "sent_on": et(today()),
            "vastab_tahtajale": deadline_revision(matter),
        },
    )
    assert response.status_code == 200
    (sent,) = Submission.objects.filter(matter=matter)
    _assert_met_by(matter, sent, due, specialist)
