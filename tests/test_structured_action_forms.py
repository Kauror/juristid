"""A structured next step opens its canonical form (docs/adr/0168, owner decision D4).

* **A** — `Järgmisena?` on «Kaasa liikmeid / küsi tagasisidet», «Saada Koja
  arvamus» and «Koosta kodulehe ülevaade»: `Alusta` is a link to the canonical
  form under `LISA TEEMALE`, never a simplified editor of its own, and drawing
  or following it writes nothing. A generic step keeps its small editor.
* **B** — without scripting the link opens that form on the server
  (`?ava=…&samm=…`), naming the step; a foreign or finished step is ignored.
* **C** — the form's save finishes **the step that opened it** in the same
  transaction (`samm`, checked under the lock): on this Matter, of this form's
  operation, still ahead. A second press, an invalid save, a wrong or foreign
  step writes nothing more.
* **D** — a current step started from such a step (before this round, or
  carried by `Muuda`) is finished through the same form, once.
* **E** — «Ootame tagasisidet» → `Tehtud` opens `Lisa tagasiside` for that
  round; only its successful save ends the wait.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.enums import WebsiteOverviewStatus
from app.matters.models import MatterEngagement, MatterExternalPosition, MatterWebsiteOverview
from app.submissions.enums import SubmissionStatus
from app.submissions.models import Submission
from app.workflow.enums import ActionStatus, PlanStepOperation, PlanStepState
from app.workflow.models import MatterPlanStep, NextAction
from app.workflow.plan import (
    STEP_NOT_OPEN,
    STEP_WRONG_OPERATION,
    activate_plan_step,
    plan_revision,
    plan_steps_of,
    seed_standard_plan,
    skip_plan_step,
)
from tests import factories
from tests.follow_ups import day, et, pdf

pytestmark = pytest.mark.django_db

READ, OVERVIEW, CONSULT, SEND = (
    "read-material",
    "website-overview",
    "consult-members",
    "send-opinion",
)
OVERVIEW_URL = "https://www.koda.ee/et/meie-moju/hetkel-kasil/naidis-ulevaade"
NEWS_URL = "https://www.koda.ee/et/uudised/naidis-uudis"


@pytest.fixture
def planned(specialist):
    matter = factories.MatterFactory(owner=specialist)
    seed_standard_plan(matter=matter, actor=specialist)
    return matter


@pytest.fixture
def ministry():
    return factories.OrganisationFactory(name="Sünteetiline ministeerium")


def _step(matter, key) -> MatterPlanStep:
    return MatterPlanStep.objects.get(matter=matter, template_step_key=key)


def _ahead_to(matter, actor, key) -> MatterPlanStep:
    """Dismiss every step before ``key``, so it is the suggestion."""
    for step in plan_steps_of(matter):
        if step.template_step_key == key:
            return step
        skip_plan_step(
            step=step, actor=actor, expected_revision=plan_revision(plan_steps_of(matter))
        )
    raise AssertionError(key)


def _page(client, matter, query: str = "") -> str:
    response = client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk}) + query)
    assert response.status_code == 200
    return response.content.decode()


def _zone(body: str) -> str:
    return body[body.index('id="praegune-tegevus"') : body.index('id="lisa-teemale"')]


def _post(client, name, matter, data):
    return client.post(
        reverse(f"matters:{name}", kwargs={"pk": matter.pk}),
        data,
        headers={"HX-Request": "true"},
    )


def _round(client, matter, step=None, **extra):
    data = {"audience": "Liikmed", "occurred_on": et(day(0)), **extra}
    if step is not None:
        data["samm"] = str(step.pk)
    return _post(client, "add_engagement_compact", matter, data)


def _opinion(client, matter, ministry, step=None, **extra):
    data = {
        "upload": pdf(),
        "recipients": [str(ministry.pk)],
        "sent_on": et(day(0)),
        "summary": "Koja arvamus.",
        **extra,
    }
    if step is not None:
        data["samm"] = str(step.pk)
    return _post(client, "add_koda_opinion", matter, data)


def _completions(step) -> int:
    return ChangeEvent.objects.filter(
        event_type=ChangeEventType.PLAN_STEP_COMPLETED, object_id=step.pk
    ).count()


def _nothing_written(matter) -> None:
    assert not NextAction.objects.filter(matter=matter).exists()
    assert not MatterEngagement.objects.filter(matter=matter).exists()
    assert not Submission.objects.filter(matter=matter).exists()
    assert not MatterWebsiteOverview.objects.filter(matter=matter).exists()


# ---------------------------------------------------------------------------
# A — the suggestion's `Alusta` is a link to the canonical form
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "slug", "panel"),
    [
        (CONSULT, "kaasamine", "kaasamine-alusta"),
        (SEND, "koja-arvamus", "arvamus-koja"),
        (OVERVIEW, "ulevaade", "lisa-koduleht"),
    ],
)
def test_a_structured_alusta_links_to_the_canonical_form(
    signed_in, specialist, planned, key, slug, panel
):
    step = _ahead_to(planned, specialist, key)

    zone = _zone(_page(signed_in, planned))

    assert f'data-open-canonical="{panel}"' in zone
    assert f'data-launch-step="{step.pk}"' in zone
    assert f"?ava={slug}&amp;samm={step.pk}#{panel}" in zone
    # No simplified editor of its own under `PRAEGUNE TEGEVUS`.
    assert 'id="alusta-samm"' not in zone
    assert "/alusta/" not in zone
    assert 'id="id_alusta_text"' not in zone
    _nothing_written(planned)


def test_a_generic_step_keeps_its_own_alusta_editor(signed_in, planned):
    zone = _zone(_page(signed_in, planned))

    assert 'id="alusta-samm"' in zone
    assert 'id="id_alusta_text"' in zone
    assert "data-open-canonical" not in zone


def test_a_reader_is_offered_no_launch(client, reader, planned):
    client.force_login(reader)
    _ahead_to(planned, planned.owner, CONSULT)

    body = _page(client, planned, f"?ava=kaasamine&samm={_step(planned, CONSULT).pk}")

    assert "data-open-canonical" not in body
    assert 'name="samm"' not in body


# ---------------------------------------------------------------------------
# B — `?ava=` opens the form on the server, naming the step; nothing written
# ---------------------------------------------------------------------------


def _checked(body: str, radio_id: str) -> bool:
    start = body.index(f'id="{radio_id}"')
    tag = body[body.rindex("<input", 0, start) : body.index(">", start)]
    return "checked" in tag


def _samm_value(body: str, form_marker: str) -> str:
    form = body[body.index(form_marker) :]
    field = form[form.index('name="samm"') :]
    start = field.index('value="') + len('value="')
    return field[start : field.index('"', start)]


def test_b_ava_kaasamine_opens_alusta_kaasamist_with_the_step(signed_in, specialist, planned):
    step = _ahead_to(planned, specialist, CONSULT)

    body = _page(signed_in, planned, f"?ava=kaasamine&samm={step.pk}")

    assert _checked(body, "lisa-kaasamine-valik")
    assert _checked(body, "kaasamine-alusta-valik")
    assert _samm_value(body, "/lisa/kaasamine/") == str(step.pk)
    form = body[body.index("/lisa/kaasamine/") :]
    note = form[form.index("data-launch-note") : form.index("</p>", form.index("data-launch-note"))]
    assert "hidden" not in note.split(">")[0]
    assert step.title in note
    _nothing_written(planned)


def test_b_ava_koja_arvamus_opens_the_opinion_form(signed_in, specialist, planned):
    step = _ahead_to(planned, specialist, SEND)

    body = _page(signed_in, planned, f"?ava=koja-arvamus&samm={step.pk}")

    assert _checked(body, "lisa-arvamus-valik")
    assert _checked(body, "arvamus-koja-valik")
    assert _samm_value(body, "/lisa/koja-arvamus/") == str(step.pk)
    _nothing_written(planned)


def test_b_a_foreign_or_finished_step_is_not_named(signed_in, specialist, planned):
    other = factories.MatterFactory(owner=specialist)
    seed_standard_plan(matter=other, actor=specialist)
    foreign = _step(other, CONSULT)

    body = _page(signed_in, planned, f"?ava=kaasamine&samm={foreign.pk}")
    assert _samm_value(body, "/lisa/kaasamine/") == ""

    skipped = _step(planned, CONSULT)
    skip_plan_step(
        step=skipped, actor=specialist, expected_revision=plan_revision(plan_steps_of(planned))
    )
    body = _page(signed_in, planned, f"?ava=kaasamine&samm={skipped.pk}")
    assert _samm_value(body, "/lisa/kaasamine/") == ""
    body = _page(signed_in, planned, "?ava=kaasamine&samm=pole-id")
    assert _samm_value(body, "/lisa/kaasamine/") == ""


# ---------------------------------------------------------------------------
# C — the save finishes the step that opened the form
# ---------------------------------------------------------------------------


def test_c_alusta_kaasamist_finishes_its_step_and_the_round_waits(signed_in, specialist, planned):
    step = _ahead_to(planned, specialist, CONSULT)

    response = _round(signed_in, planned, step)

    assert response.status_code == 200
    engagement = MatterEngagement.objects.get(matter=planned)
    step.refresh_from_db()
    assert step.state == PlanStepState.COMPLETED
    assert step.fulfilled_by_operation == PlanStepOperation.ENGAGEMENT
    assert step.fulfilled_by_record == engagement.pk
    assert _completions(step) == 1
    # No action invented to be finished: the round is the waiting state.
    assert not NextAction.objects.filter(matter=planned).exists()
    zone = _zone(_page(signed_in, planned))
    assert "Ootame tagasisidet" in zone
    assert f'data-feedback-wait="{engagement.pk}"' in zone
    # The next suggestion is the opinion, never «Koonda tagasiside …».
    assert "Saada Koja arvamus" in zone
    assert "Koonda tagasiside" not in zone


def test_c_a_second_press_writes_nothing_more(signed_in, specialist, planned):
    step = _ahead_to(planned, specialist, CONSULT)
    assert _round(signed_in, planned, step).status_code == 200

    again = _round(signed_in, planned, step)

    assert again.status_code == 400
    assert STEP_NOT_OPEN in again.content.decode()
    assert MatterEngagement.objects.filter(matter=planned).count() == 1
    assert _completions(step) == 1


def test_c_an_invalid_save_writes_nothing_and_keeps_the_step_named(signed_in, specialist, planned):
    step = _ahead_to(planned, specialist, CONSULT)

    refused = _round(signed_in, planned, step, audience="")

    assert refused.status_code == 400
    body = refused.content.decode()
    assert "Kirjuta, keda kaasad." in body
    # The corrected save still finishes the same step.
    assert _samm_value(body, "/lisa/kaasamine/") == str(step.pk)
    step.refresh_from_db()
    assert step.state == PlanStepState.SUGGESTED
    _nothing_written(planned)

    assert _round(signed_in, planned, step).status_code == 200
    step.refresh_from_db()
    assert step.state == PlanStepState.COMPLETED


def test_c_a_step_of_another_kind_refuses_the_whole_save(signed_in, specialist, planned):
    opinion_step = _step(planned, SEND)

    refused = _round(signed_in, planned, opinion_step)

    assert refused.status_code == 400
    assert STEP_WRONG_OPERATION in refused.content.decode()
    _nothing_written(planned)
    opinion_step.refresh_from_db()
    assert opinion_step.state == PlanStepState.SUGGESTED


def test_c_a_step_of_another_matter_is_a_404(signed_in, specialist, planned):
    other = factories.MatterFactory(owner=specialist)
    seed_standard_plan(matter=other, actor=specialist)

    response = _round(signed_in, planned, _step(other, CONSULT))

    assert response.status_code == 404
    _nothing_written(planned)
    assert _step(other, CONSULT).state == PlanStepState.SUGGESTED
    garbled = _post(
        signed_in,
        "add_engagement_compact",
        planned,
        {"audience": "Liikmed", "samm": "pole-id"},
    )
    assert garbled.status_code == 404
    assert (
        _post(
            signed_in,
            "add_engagement_compact",
            planned,
            {"audience": "Liikmed", "samm": str(uuid.uuid4())},
        ).status_code
        == 404
    )
    _nothing_written(planned)


def test_c_a_dismissed_step_refuses(signed_in, specialist, planned):
    step = _step(planned, CONSULT)
    skip_plan_step(
        step=step, actor=specialist, expected_revision=plan_revision(plan_steps_of(planned))
    )

    refused = _round(signed_in, planned, step)

    assert refused.status_code == 400
    assert STEP_NOT_OPEN in refused.content.decode()
    _nothing_written(planned)


def test_c_without_a_step_the_form_behaves_as_before(signed_in, specialist, planned):
    _ahead_to(planned, specialist, CONSULT)

    assert _round(signed_in, planned).status_code == 200

    assert _step(planned, CONSULT).state == PlanStepState.SUGGESTED
    assert not ChangeEvent.objects.filter(event_type=ChangeEventType.PLAN_STEP_COMPLETED).exists()


def test_c_koja_arvamus_finishes_saada_koja_arvamus(signed_in, specialist, planned, ministry):
    step = _ahead_to(planned, specialist, SEND)

    response = _opinion(signed_in, planned, ministry, step)

    assert response.status_code == 200, response.content.decode()[:500]
    submission = Submission.objects.get(matter=planned)
    assert submission.status == SubmissionStatus.SENT
    step.refresh_from_db()
    assert step.state == PlanStepState.COMPLETED
    assert step.fulfilled_by_record == submission.pk
    assert _completions(step) == 1


def test_c_an_invalid_opinion_writes_nothing(signed_in, specialist, planned, ministry):
    step = _ahead_to(planned, specialist, SEND)
    data = {"recipients": [str(ministry.pk)], "sent_on": et(day(0)), "samm": str(step.pk)}

    refused = _post(signed_in, "add_koda_opinion", planned, data)

    assert refused.status_code == 400
    assert _samm_value(refused.content.decode(), "/lisa/koja-arvamus/") == str(step.pk)
    step.refresh_from_db()
    assert step.state == PlanStepState.SUGGESTED
    _nothing_written(planned)


def test_c_a_repeated_opinion_post_sends_one(signed_in, specialist, planned, ministry):
    step = _ahead_to(planned, specialist, SEND)
    token = str(uuid.uuid4())

    first = _opinion(signed_in, planned, ministry, step, salvestus=token)
    second = _opinion(signed_in, planned, ministry, step, salvestus=token)

    assert first.status_code == 200
    assert second.status_code == 400
    assert Submission.objects.filter(matter=planned).count() == 1
    assert _completions(step) == 1


def test_c_a_published_overview_finishes_its_step(signed_in, specialist, planned):
    step = _ahead_to(planned, specialist, OVERVIEW)
    data = {"url": OVERVIEW_URL, "published_on": et(day(0)), "samm": str(step.pk)}

    assert _post(signed_in, "add_website_overview", planned, data).status_code == 200

    overview = MatterWebsiteOverview.objects.get(matter=planned)
    assert overview.status == WebsiteOverviewStatus.PUBLISHED
    step.refresh_from_db()
    assert step.state == PlanStepState.COMPLETED
    assert step.fulfilled_by_record == overview.pk


def test_c_a_news_item_does_not_finish_the_overview_step(signed_in, specialist, planned):
    step = _ahead_to(planned, specialist, OVERVIEW)
    data = {"url": NEWS_URL, "published_on": et(day(0)), "samm": str(step.pk)}

    assert _post(signed_in, "add_website_overview", planned, data).status_code == 200

    step.refresh_from_db()
    assert step.state == PlanStepState.SUGGESTED


# ---------------------------------------------------------------------------
# D — a current structured step is finished through its canonical form
# ---------------------------------------------------------------------------


def test_d_a_current_opinion_step_offers_the_canonical_form(signed_in, specialist, planned):
    step = _step(planned, SEND)
    activate_plan_step(matter=planned, step=step, actor=specialist)

    zone = _zone(_page(signed_in, planned))

    assert 'data-open-canonical="arvamus-koja"' in zone
    assert f'data-launch-step="{step.pk}"' in zone
    assert 'id="id_praegune_body"' not in zone


def test_d_saving_it_completes_the_step_and_its_action_once(
    signed_in, specialist, planned, ministry
):
    step = _step(planned, SEND)
    action = activate_plan_step(matter=planned, step=step, actor=specialist)

    response = _opinion(signed_in, planned, ministry, step)

    assert response.status_code == 200, response.content.decode()[:500]
    action.refresh_from_db()
    step.refresh_from_db()
    assert action.status == ActionStatus.COMPLETED
    assert step.state == PlanStepState.COMPLETED
    assert step.fulfilled_by_record == Submission.objects.get(matter=planned).pk
    assert _completions(step) == 1


def test_d_with_the_tick_too_it_still_completes_once(signed_in, specialist, planned, ministry):
    step = _step(planned, SEND)
    action = activate_plan_step(matter=planned, step=step, actor=specialist)

    response = _opinion(signed_in, planned, ministry, step, complete_action=str(action.pk))

    assert response.status_code == 200, response.content.decode()[:500]
    action.refresh_from_db()
    assert action.status == ActionStatus.COMPLETED
    assert _completions(step) == 1
    assert (
        ChangeEvent.objects.filter(
            event_type=ChangeEventType.NEXT_ACTION_COMPLETED, object_id=action.pk
        ).count()
        == 1
    )


def test_d_a_generic_current_step_keeps_mida_tegid(signed_in, specialist, planned):
    activate_plan_step(matter=planned, step=_step(planned, READ), actor=specialist)

    zone = _zone(_page(signed_in, planned))

    assert 'id="id_praegune_body"' in zone
    assert "data-open-canonical" not in zone


def test_d_a_step_the_record_already_shows_done_keeps_plain_tehtud(signed_in, specialist, planned):
    step = _step(planned, CONSULT)
    action = activate_plan_step(matter=planned, step=step, actor=specialist)
    NextAction.objects.filter(pk=action.pk).update(
        created_at=timezone.now() - dt.timedelta(hours=1)
    )
    assert _round(signed_in, planned).status_code == 200

    zone = _zone(_page(signed_in, planned))

    assert "data-consistency-alert" in zone
    assert 'id="id_praegune_body"' in zone
    assert "data-open-canonical" not in zone


# ---------------------------------------------------------------------------
# E — «Ootame tagasisidet» → `Tehtud` → `Lisa tagasiside`
# ---------------------------------------------------------------------------


def test_e_tehtud_links_to_lisa_tagasiside_for_that_round(signed_in, specialist, planned):
    assert _round(signed_in, planned).status_code == 200
    engagement = MatterEngagement.objects.get(matter=planned)

    zone = _zone(_page(signed_in, planned))

    assert f'data-open-feedback="{engagement.pk}"' in zone
    assert f"?ava=tagasiside&amp;kaasamine={engagement.pk}#kaasamine-tagasiside" in zone


def test_e_without_scripting_the_link_opens_the_form_with_the_round(signed_in, specialist, planned):
    assert _round(signed_in, planned, audience="Esimene").status_code == 200
    assert _round(signed_in, planned, audience="Teine").status_code == 200
    first = MatterEngagement.objects.get(matter=planned, title="Esimene")

    body = _page(signed_in, planned, f"?ava=tagasiside&kaasamine={first.pk}")

    assert _checked(body, "lisa-kaasamine-valik")
    assert _checked(body, "kaasamine-tagasiside-valik")
    select = body[body.index('id="id_kaasamise_tagasiside_engagement"') :]
    select = select[: select.index("</select>")]
    assert f'value="{first.pk}" selected' in select
    first.refresh_from_db()
    assert first.feedback_closed_at is None


def test_e_only_a_successful_save_ends_the_wait(signed_in, specialist, planned):
    assert _round(signed_in, planned).status_code == 200
    engagement = MatterEngagement.objects.get(matter=planned)

    refused = _post(
        signed_in,
        "add_engagement_reply",
        planned,
        {"engagement": str(engagement.pk), "summary": "", "stated_on": et(day(0))},
    )
    assert refused.status_code == 400
    engagement.refresh_from_db()
    assert engagement.feedback_closed_at is None
    assert not MatterExternalPosition.objects.filter(matter=planned).exists()

    saved = _post(
        signed_in,
        "add_engagement_reply",
        planned,
        {"engagement": str(engagement.pk), "summary": "Liikmed vastasid.", "stated_on": ""},
    )
    assert saved.status_code == 200
    engagement.refresh_from_db()
    assert engagement.feedback_closed_at is not None
    assert "Ootame tagasisidet" not in _zone(_page(signed_in, planned))
