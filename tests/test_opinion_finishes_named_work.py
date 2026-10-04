"""One `Koja arvamus` save can finish the work it answers — only the work named.

Historical regression of 2026-10-04, UX-005 and UX-002: sending the opinion left
the deadline, the rounds and the step each to be closed by hand, and the plan
never noticed work recorded from `LISA TEEMALE`.

Protected here:

* the opinion may name the current `Arvamuse tähtaeg` it answers, specific
  rounds whose wait it ends, and one plan step ahead it fulfils — and nothing
  unnamed moves;
* with two rounds open, finishing one leaves the other open;
* any part refused refuses the whole save: no opinion, no file, no completion;
* a second press of the same drawn form records nothing twice;
* a round or overview recorded from `LISA TEEMALE` may fulfil a plan step of its
  kind, without an action being invented, and the current step is not offered;
* the history keeps one row for the act, with what it finished under it;
* a closed file's plan is folded away and offers nothing to start.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from app.matters.enums import EngagementKind, ResponseDeadlineOutcome
from app.matters.models import MatterEngagement, MatterResponseDeadline
from app.matters.response_deadlines import change_response_deadline, deadline_revision
from app.matters.services import add_engagement, close_matter, engagement_revision_token
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.workflow.enums import ActionStatus, Disposition, PlanStepState
from app.workflow.models import MatterPlanStep, NextAction
from app.workflow.plan import seed_standard_plan
from app.workflow.services import set_next_action
from tests import factories

pytestmark = pytest.mark.django_db


def _day(n: int) -> dt.date:
    return timezone.localdate() + dt.timedelta(days=n)


def _et(day: dt.date) -> str:
    return f"{day.day}.{day.month}.{day.year}"


def _pdf(name: str = "arvamus.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name, b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF", content_type="application/pdf"
    )


@pytest.fixture
def organisation():
    return factories.OrganisationFactory(name="Sünteetiline ministeerium")


def _opinion(client, matter, organisation, **fields):
    payload = {
        "upload": _pdf(),
        "recipients": [str(organisation.pk)],
        "sent_on": _et(timezone.localdate()),
        "summary": "Sünteetiline arvamus.",
    }
    payload.update(fields)
    return client.post(
        reverse("matters:add_koda_opinion", kwargs={"pk": matter.pk}),
        payload,
        headers={"HX-Request": "true"},
    )


def _round(matter, title, actor):
    return add_engagement(
        matter=matter,
        kind=EngagementKind.SURVEY,
        title=title,
        occurred_on=_day(-3),
        feedback_deadline=_day(4),
        actor=actor,
    )


def _round_value(engagement) -> str:
    engagement.refresh_from_db()
    return f"{engagement.pk}:{engagement_revision_token(engagement)}"


def _requested(matter, actor, deadline):
    change_response_deadline(matter=matter, deadline=deadline, actor=actor)
    matter.refresh_from_db()
    return matter


# ---------------------------------------------------------------------------
# A — nothing unnamed moves
# ---------------------------------------------------------------------------


def test_an_opinion_naming_nothing_finishes_nothing(signed_in, specialist, organisation):
    matter = _requested(factories.MatterFactory(owner=specialist), specialist, _day(5))
    engagement = _round(matter, "Liikmete küsitlus", specialist)
    action = set_next_action(matter=matter, text="Koostan arvamuse", actor=specialist)

    response = _opinion(signed_in, matter, organisation)

    assert response.status_code == 200
    assert Submission.objects.filter(matter=matter, status=SubmissionStatus.SENT).count() == 1
    matter.refresh_from_db()
    engagement.refresh_from_db()
    action.refresh_from_db()
    assert matter.response_deadline == _day(5)
    assert engagement.feedback_closed_at is None
    assert action.status == ActionStatus.OPEN


def test_the_opinion_answers_the_deadline_it_names(signed_in, specialist, organisation):
    matter = _requested(factories.MatterFactory(owner=specialist), specialist, _day(-2))

    response = _opinion(signed_in, matter, organisation, vastab_tahtajale=deadline_revision(matter))

    assert response.status_code == 200
    matter.refresh_from_db()
    assert matter.response_deadline is None
    (row,) = MatterResponseDeadline.objects.filter(matter=matter)
    assert row.outcome == ResponseDeadlineOutcome.ANSWERED
    assert row.submission == Submission.objects.get(matter=matter)
    page = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    assert f"Arvamuse tähtaeg {_et(_day(-2))}" in page


def test_of_two_open_rounds_only_the_named_one_is_finished(signed_in, specialist, organisation):
    matter = factories.MatterFactory(owner=specialist)
    first = _round(matter, "Esimene voor", specialist)
    second = _round(matter, "Teine voor", specialist)

    _opinion(signed_in, matter, organisation, lopeta_kaasamine=[_round_value(first)])

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.feedback_closed_at is not None
    assert first.feedback_received == ""
    assert second.feedback_closed_at is None


def test_the_history_keeps_one_row_with_what_it_finished(signed_in, specialist, organisation):
    from app.matters.timeline import matter_timeline

    matter = _requested(factories.MatterFactory(owner=specialist), specialist, _day(3))
    engagement = _round(matter, "Liikmete küsitlus", specialist)

    _opinion(
        signed_in,
        matter,
        organisation,
        vastab_tahtajale=deadline_revision(matter),
        lopeta_kaasamine=[_round_value(engagement)],
    )

    rows, _more = matter_timeline(matter=matter, user=specialist)
    (sent,) = [row for row in rows if isinstance(row.record, Submission)]
    assert sent.answered_deadline == _et(_day(3))
    assert sent.closed_rounds == ("Liikmete küsitlus",)


# ---------------------------------------------------------------------------
# B — refusals refuse the whole save
# ---------------------------------------------------------------------------


def test_a_deadline_changed_meanwhile_refuses_the_whole_save(signed_in, specialist, organisation):
    matter = _requested(factories.MatterFactory(owner=specialist), specialist, _day(3))
    engagement = _round(matter, "Liikmete küsitlus", specialist)
    stale = deadline_revision(matter)
    change_response_deadline(matter=matter, deadline=_day(9), actor=specialist, change="MOVED")

    _opinion(
        signed_in,
        matter,
        organisation,
        vastab_tahtajale=stale,
        lopeta_kaasamine=[_round_value(engagement)],
    )

    assert not Submission.objects.filter(matter=matter).exists()
    engagement.refresh_from_db()
    assert engagement.feedback_closed_at is None
    matter.refresh_from_db()
    assert matter.response_deadline == _day(9)


def test_another_files_round_refuses_the_save(signed_in, specialist, organisation):
    matter = factories.MatterFactory(owner=specialist)
    elsewhere = _round(factories.MatterFactory(owner=specialist), "Võõras voor", specialist)

    _opinion(signed_in, matter, organisation, lopeta_kaasamine=[_round_value(elsewhere)])

    assert not Submission.objects.filter(matter=matter).exists()
    elsewhere.refresh_from_db()
    assert elsewhere.feedback_closed_at is None


def test_a_rejected_file_finishes_nothing(signed_in, specialist, organisation):
    matter = _requested(factories.MatterFactory(owner=specialist), specialist, _day(3))
    engagement = _round(matter, "Liikmete küsitlus", specialist)

    _opinion(
        signed_in,
        matter,
        organisation,
        upload=SimpleUploadedFile("tühi.pdf", b"", content_type="application/pdf"),
        vastab_tahtajale=deadline_revision(matter),
        lopeta_kaasamine=[_round_value(engagement)],
    )

    assert not Submission.objects.filter(matter=matter).exists()
    matter.refresh_from_db()
    engagement.refresh_from_db()
    assert matter.response_deadline == _day(3)
    assert engagement.feedback_closed_at is None


def test_a_second_press_of_the_same_form_records_nothing_twice(signed_in, specialist, organisation):
    matter = factories.MatterFactory(owner=specialist)
    token = str(uuid.uuid4())

    _opinion(signed_in, matter, organisation, salvestus=token)
    second = _opinion(signed_in, matter, organisation, salvestus=token)

    assert Submission.objects.filter(matter=matter).count() == 1
    assert "juba salvestatud" in second.content.decode()


# ---------------------------------------------------------------------------
# C — the plan, from either door
# ---------------------------------------------------------------------------


def _plan(matter, actor):
    seed_standard_plan(matter=matter, actor=actor)
    return {step.operation: step for step in MatterPlanStep.objects.filter(matter=matter)}


def test_an_opinion_from_lisa_teemale_fulfils_the_named_plan_step(
    signed_in, specialist, organisation
):
    matter = factories.MatterFactory(owner=specialist)
    steps = _plan(matter, specialist)
    other = set_next_action(matter=matter, text="Muu töö", actor=specialist)
    send_step = steps["SUBMISSION"]

    _opinion(signed_in, matter, organisation, taidab_sammu=str(send_step.pk))

    send_step.refresh_from_db()
    assert send_step.state == PlanStepState.COMPLETED
    assert send_step.fulfilled_by_record == Submission.objects.get(matter=matter).pk
    other.refresh_from_db()
    assert other.status == ActionStatus.OPEN
    assert NextAction.objects.filter(matter=matter).count() == 1


def test_a_round_and_an_overview_fulfil_their_steps_without_an_invented_action(
    signed_in, specialist
):
    matter = factories.MatterFactory(owner=specialist)
    steps = _plan(matter, specialist)

    signed_in.post(
        reverse("matters:add_engagement_compact", kwargs={"pk": matter.pk}),
        {"audience": "Liikmed (sünteetiline)", "taidab_sammu": str(steps["ENGAGEMENT"].pk)},
        headers={"HX-Request": "true"},
    )
    refused = signed_in.post(
        reverse("matters:add_website_overview", kwargs={"pk": matter.pk}),
        {"taidab_sammu": str(steps["WEBSITE_OVERVIEW"].pk)},
        headers={"HX-Request": "true"},
    )

    consult = MatterPlanStep.objects.get(pk=steps["ENGAGEMENT"].pk)
    overview = MatterPlanStep.objects.get(pk=steps["WEBSITE_OVERVIEW"].pk)
    assert consult.state == PlanStepState.COMPLETED
    assert consult.fulfilled_by_record == MatterEngagement.objects.get(matter=matter).pk
    # A plan, not a publication, does not do the overview step's work.
    assert overview.state == PlanStepState.SUGGESTED
    assert refused.status_code in (200, 400)
    assert not NextAction.objects.filter(matter=matter).exists()


def test_the_current_step_is_finished_by_its_own_action_not_by_naming(
    signed_in, specialist, organisation
):
    from app.workflow.plan import activate_plan_step

    matter = factories.MatterFactory(owner=specialist)
    steps = _plan(matter, specialist)
    activate_plan_step(matter=matter, step=steps["SUBMISSION"], actor=specialist)

    _opinion(signed_in, matter, organisation, taidab_sammu=str(steps["SUBMISSION"].pk))

    assert not Submission.objects.filter(matter=matter).exists()
    page = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()
    assert f'<option value="{steps["SUBMISSION"].pk}"' not in page


def test_a_closed_files_plan_is_folded_and_offers_nothing_to_start(signed_in, specialist):
    matter = factories.MatterFactory(owner=specialist)
    _plan(matter, specialist)
    close_matter(matter=matter, disposition=Disposition.COMPLETED, actor=specialist)

    page = signed_in.get(
        reverse("matters:matter_detail", kwargs={"pk": matter.pk})
    ).content.decode()

    assert "Tööplaan teema sulgemise ajal" in page
    assert "Soovitatud järgmisena" not in page
    assert ">Alusta<" not in page
    assert MatterPlanStep.objects.filter(matter=matter, state=PlanStepState.SUGGESTED).count() == 5
