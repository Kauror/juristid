"""Where an `Arvamuse järelkontroll` is seen and acted on (docs/adr/0146 §7–§8, §10).

* **work surfaces** — Minu asjad, Osakond and the Teema page show the check,
  its day, its opinion and its addressees; a planned check whose day has passed
  reads late; the row opens the check's own row;
* **the Teema rows** — a planned or current check draws `✓ Tehtud` (what was
  found) and `Muuda` (its day), never `×`, never `Mida tegid?`, and the
  `Koja arvamus` panel never offers to tick it;
* **the two endpoints** — refusals come back into the check's own row;
* **visibility** — a reader refused the opinion is refused its check, its line,
  its events and its link;
* **closure** — every form that can close the Matter asks first, and closes
  once the person confirms;
* **history** — `Teema käik` reads the send with its check under it, and a
  check with what it found.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.audit.visibility import scope_change_events
from app.core.enums import Visibility
from app.documents.enums import DocumentRole
from app.documents.models import Document
from app.documents.services import add_evidence_version, create_document
from app.matters.services import create_matter
from app.matters.work_items import work_items
from app.submissions.enums import SentAtPrecision
from app.submissions.models import Submission
from app.submissions.services import register_sent_opinion_on_open_matter
from app.workflow.enums import ActionStatus, FollowUpOutcome
from app.workflow.follow_ups import (
    FOLLOW_UP_CLOSURE_WARNING,
    FOLLOW_UP_DATE_IN_THE_PAST,
    FOLLOW_UP_NEEDS_OUTCOME,
    FOLLOW_UP_TEXT_ONE,
    NEXT_CHECK_NEEDS_DATE,
    reschedule_check,
)
from app.workflow.models import NextAction, StageVocabulary
from app.workflow.services import set_next_action_for_new_work
from tests import factories
from tests.follow_ups import (
    PDF,
    active_check,
    day,
    et,
    mark_sent,
    pdf,
    post,
    register_sent,
    send_koja_arvamus,
)

pytestmark = pytest.mark.django_db

OPTION = "Märgi praegune tegevus tehtuks"


@pytest.fixture
def ministry():
    return factories.OrganisationFactory(name="Sünteetiline ministeerium")


def _teema(client, matter) -> str:
    return client.get(reverse("matters:matter_detail", kwargs={"pk": matter.pk})).content.decode()


def _zone(body: str) -> str:
    start = body.index('id="praegune-tegevus"')
    return body[start : body.index("</section>", start)]


def _row(body: str, check) -> str:
    """The check's own row — planned `<li>` or current task line."""
    start = body.index(f'id="jarelkontroll-{check.pk}"')
    ends = [body.find(marker, start) for marker in ("</li>", "</section>")]
    return body[start : min(end for end in ends if end != -1)]


def _stage(key: str) -> StageVocabulary:
    return StageVocabulary.objects.get(key=key)


def _current(check):
    NextAction.objects.filter(pk=check.pk).update(status=ActionStatus.OPEN)
    return NextAction.objects.get(pk=check.pk)


# ---------------------------------------------------------------------------
# Work surfaces
# ---------------------------------------------------------------------------


def test_an_upcoming_check_is_on_minu_asjad_with_its_opinion(
    signed_in, normal_matter, specialist, ministry
):
    check = active_check(mark_sent(normal_matter, specialist, [ministry]))

    page = signed_in.get(reverse("matters:my_work")).content.decode()

    assert FOLLOW_UP_TEXT_ONE in page
    assert f"Koja arvamus {et(day(0))} · Sünteetiline ministeerium" in page
    assert f"#jarelkontroll-{check.pk}" in page


def test_an_overdue_planned_check_reads_late_on_every_work_surface(
    signed_in, normal_matter, specialist, ministry
):
    opinion = register_sent(normal_matter, specialist, [ministry], day(-40))
    check = active_check(opinion)
    assert check.status == ActionStatus.PLANNED

    (item,) = [row for row in work_items(specialist) if row.object_id == check.pk]
    assert item.is_overdue
    assert item.short_date == "10 p üle"
    assert item.is_follow_up
    assert item.record_url.endswith(f"#jarelkontroll-{check.pk}")

    minu = signed_in.get(reverse("matters:my_work")).content.decode()
    assert "workrow2--overdue" in minu
    row = _row(_teema(signed_in, normal_matter), check)
    assert "curact__planneddate--overdue" in row
    assert "10 p" in row


def test_a_planned_action_that_is_not_a_check_is_not_called_late(
    signed_in, normal_matter, specialist
):
    from app.workflow.services import add_planned_action

    set_next_action_for_new_work(
        matter=normal_matter, text="Loe eelnõu", target_date=day(5), actor=specialist
    )
    planned = add_planned_action(
        matter=normal_matter, text="Tavaline plaan", target_date=day(1), actor=specialist
    )
    NextAction.objects.filter(pk=planned.pk).update(target_date=day(-3))
    planned.refresh_from_db()

    assert not planned.is_overdue()


def test_osakond_lists_an_overdue_check_for_the_department_head(
    client, normal_matter, specialist, department_head, ministry
):
    register_sent(normal_matter, specialist, [ministry], day(-40))
    client.force_login(department_head)

    page = client.get(reverse("matters:department")).content.decode()

    assert FOLLOW_UP_TEXT_ONE in page
    assert "Sünteetiline ministeerium" in page


def test_a_due_check_is_actionable_beside_an_unrelated_current_action(
    signed_in, normal_matter, specialist, ministry
):
    set_next_action_for_new_work(
        matter=normal_matter, text="Muu töö", target_date=day(3), actor=specialist
    )
    check = active_check(register_sent(normal_matter, specialist, [ministry], day(-31)))

    row = _row(_teema(signed_in, normal_matter), check)

    assert (
        reverse("matters:check_follow_up", kwargs={"pk": normal_matter.pk, "action_id": check.pk})
        in row
    )


# ---------------------------------------------------------------------------
# The Teema rows
# ---------------------------------------------------------------------------


def test_a_planned_check_row_has_its_own_controls_and_no_remove(
    signed_in, normal_matter, specialist, ministry
):
    opinion = mark_sent(normal_matter, specialist, [ministry])
    check = active_check(opinion)

    row = _row(_teema(signed_in, normal_matter), check)

    assert "✓ Tehtud" in row
    assert "Muuda" in row
    assert "Kustuta planeeritud tegevus" not in row
    assert "Vastus saabunud" in row
    assert "Vastust ei ole — kontrollin uuesti" in row
    assert "Lõpetan jälgimise" in row
    assert (
        reverse(
            "matters:reschedule_follow_up", kwargs={"pk": normal_matter.pk, "action_id": check.pk}
        )
        in row
    )
    assert reverse("documents:download", kwargs={"pk": opinion.final_version_id}) in row
    assert f"Koja arvamus {et(day(0))} · Sünteetiline ministeerium" in row


def test_a_current_check_offers_its_own_form_and_not_mida_tegid(
    signed_in, normal_matter, specialist, ministry
):
    check = _current(active_check(mark_sent(normal_matter, specialist, [ministry])))

    zone = _zone(_teema(signed_in, normal_matter))

    assert 'id="tehtud-valik"' not in zone
    assert 'id="lisa-jargmine"' not in zone
    assert (
        reverse("matters:check_follow_up", kwargs={"pk": normal_matter.pk, "action_id": check.pk})
        in zone
    )
    assert f'id="jarelkontroll-{check.pk}"' in zone


def test_the_koja_arvamus_panel_never_offers_to_tick_a_check(
    signed_in, normal_matter, specialist, ministry
):
    _current(active_check(mark_sent(normal_matter, specialist, [ministry])))

    body = _teema(signed_in, normal_matter)
    panel = body[body.index('id="arvamus-koja"') :]
    panel = panel[: panel.index("</form>")]

    assert OPTION not in panel


# ---------------------------------------------------------------------------
# The two endpoints
# ---------------------------------------------------------------------------


def test_the_check_form_records_no_answer_and_plans_the_next_day(
    signed_in, normal_matter, specialist, ministry
):
    opinion = mark_sent(normal_matter, specialist, [ministry])
    check = active_check(opinion)

    response = post(
        signed_in,
        "check_follow_up",
        normal_matter,
        {"outcome": FollowUpOutcome.NO_RESPONSE, "next_check_on": et(day(14))},
        action_id=check.pk,
    )

    assert response.status_code == 200
    check.refresh_from_db()
    assert check.follow_up_outcome == FollowUpOutcome.NO_RESPONSE
    assert active_check(opinion).target_date == day(14)


def test_a_received_answer_posts_with_its_file(signed_in, normal_matter, specialist, ministry):
    opinion = mark_sent(normal_matter, specialist, [ministry])
    check = active_check(opinion)
    before = Document.objects.filter(matter=normal_matter).count()

    response = post(
        signed_in,
        "check_follow_up",
        normal_matter,
        {
            "outcome": FollowUpOutcome.RESPONSE_RECEIVED,
            "body": "Ministeerium vastas.",
            "attachments": [pdf("vastus.pdf")],
        },
        action_id=check.pk,
    )

    assert response.status_code == 200
    assert Document.objects.filter(matter=normal_matter).count() == before + 1
    check.refresh_from_db()
    assert check.follow_up_outcome == FollowUpOutcome.RESPONSE_RECEIVED


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({}, FOLLOW_UP_NEEDS_OUTCOME),
        ({"outcome": FollowUpOutcome.NO_RESPONSE}, NEXT_CHECK_NEEDS_DATE),
        ({"outcome": FollowUpOutcome.MONITORING_ENDED}, "Kirjuta, miks jälgimine lõpeb."),
    ],
)
def test_a_refused_check_comes_back_into_its_own_row(
    signed_in, normal_matter, specialist, ministry, data, message
):
    check = active_check(mark_sent(normal_matter, specialist, [ministry]))

    response = post(signed_in, "check_follow_up", normal_matter, data, action_id=check.pk)

    assert response.status_code == 400
    row = _row(response.content.decode(), check)
    assert message in row
    check.refresh_from_db()
    assert check.status == ActionStatus.PLANNED


def test_the_day_form_moves_the_check(signed_in, normal_matter, specialist, ministry):
    check = active_check(mark_sent(normal_matter, specialist, [ministry]))

    response = post(
        signed_in,
        "reschedule_follow_up",
        normal_matter,
        {"target_date": et(day(3))},
        action_id=check.pk,
    )

    assert response.status_code == 200
    check.refresh_from_db()
    assert check.target_date == day(3)


def test_the_day_form_refuses_the_past_in_its_row(signed_in, normal_matter, specialist, ministry):
    check = active_check(mark_sent(normal_matter, specialist, [ministry]))

    response = post(
        signed_in,
        "reschedule_follow_up",
        normal_matter,
        {"target_date": et(day(-2))},
        action_id=check.pk,
    )

    assert response.status_code == 400
    assert FOLLOW_UP_DATE_IN_THE_PAST in _row(response.content.decode(), check)


def test_a_reader_cannot_finish_or_move_a_check(
    client, normal_matter, specialist, reader, ministry
):
    check = active_check(mark_sent(normal_matter, specialist, [ministry]))
    client.force_login(reader)

    finished = post(
        client,
        "check_follow_up",
        normal_matter,
        {"outcome": FollowUpOutcome.RESPONSE_RECEIVED},
        action_id=check.pk,
    )
    moved = post(
        client,
        "reschedule_follow_up",
        normal_matter,
        {"target_date": et(day(3))},
        action_id=check.pk,
    )

    assert finished.status_code in (403, 404)
    assert moved.status_code in (403, 404)
    check.refresh_from_db()
    assert (check.status, check.target_date) == (ActionStatus.PLANNED, day(30))


def test_a_check_is_reached_only_through_its_own_matter(signed_in, specialist, ministry):
    one = factories.MatterFactory(owner=specialist)
    other = factories.MatterFactory(owner=specialist)
    check = active_check(mark_sent(one, specialist, [ministry]))

    response = post(
        signed_in,
        "check_follow_up",
        other,
        {"outcome": FollowUpOutcome.RESPONSE_RECEIVED},
        action_id=check.pk,
    )

    assert response.status_code == 404


def test_an_ordinary_planned_action_is_not_a_check(signed_in, normal_matter, specialist):
    from app.workflow.services import add_planned_action

    set_next_action_for_new_work(
        matter=normal_matter, text="Loe eelnõu", target_date=day(5), actor=specialist
    )
    planned = add_planned_action(
        matter=normal_matter, text="Tavaline plaan", target_date=day(9), actor=specialist
    )

    response = post(
        signed_in,
        "check_follow_up",
        normal_matter,
        {"outcome": FollowUpOutcome.RESPONSE_RECEIVED},
        action_id=planned.pk,
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


def _restricted_opinion(matter, actor, ministry) -> Submission:
    opinion = mark_sent(matter, actor, [ministry])
    Document.objects.filter(pk=opinion.final_version.document_id).update(
        visibility_override=Visibility.RESTRICTED
    )
    Submission.objects.filter(pk=opinion.pk).update(visibility_override=Visibility.RESTRICTED)
    opinion.refresh_from_db()
    return opinion


def test_a_reader_refused_the_opinion_is_refused_its_check(
    client, normal_matter, specialist, reader, ministry
):
    opinion = _restricted_opinion(normal_matter, specialist, ministry)
    check = active_check(opinion)
    # Scheduled while the opinion was still normal: only the live rule hides it.
    assert check.visibility_override == ""

    assert not NextAction.objects.visible_to(reader).filter(pk=check.pk).exists()
    assert NextAction.objects.visible_to(specialist).filter(pk=check.pk).exists()
    assert not [item for item in work_items(reader) if item.object_id == check.pk]
    events = ChangeEvent.objects.filter(matter=normal_matter, object_id=check.pk)
    assert events.exists()
    assert not scope_change_events(events, reader).exists()
    client.force_login(reader)
    page = _teema(client, normal_matter)
    assert FOLLOW_UP_TEXT_ONE not in page
    assert "Sünteetiline ministeerium" not in page


def test_a_check_scheduled_for_a_restricted_opinion_is_restricted(
    normal_matter, specialist, reader, ministry
):
    document = create_document(
        matter=normal_matter,
        title="Piiratud kiri",
        role=DocumentRole.KODA_SUBMISSION_FINAL,
        visibility_override=Visibility.RESTRICTED,
        created_by=specialist,
    )
    version = add_evidence_version(
        document=document,
        content=PDF,
        original_filename="p.pdf",
        mime_type="application/pdf",
        uploaded_by=specialist,
    )
    opinion = register_sent_opinion_on_open_matter(
        document=document,
        version=version,
        title="Piiratud",
        actor=specialist,
        recipients=[ministry],
        sent_at=timezone.make_aware(dt.datetime.combine(day(0), dt.time.min)),
        sent_at_precision=SentAtPrecision.DATE,
    )

    check = active_check(opinion)
    assert check.visibility_override == Visibility.RESTRICTED
    assert not NextAction.objects.visible_to(reader).filter(pk=check.pk).exists()


def test_the_opinion_line_is_read_through_the_opinion_s_own_visibility(
    normal_matter, specialist, reader, ministry
):
    from app.matters.follow_up_display import follow_up_subjects

    opinion = _restricted_opinion(normal_matter, specialist, ministry)
    check = active_check(opinion)

    assert follow_up_subjects([check], specialist)
    assert follow_up_subjects([check], reader) == {}


# ---------------------------------------------------------------------------
# Closure from each form
# ---------------------------------------------------------------------------


def _staged(owner):
    return create_matter(
        title="Pakendiseaduse muudatus", actor=owner, owner=owner, stage=_stage("consultation")
    )


def test_muuda_teemat_asks_before_closing_and_closes_once_confirmed(
    signed_in, specialist, ministry
):
    from tests.test_oigusakt_field import edit_payload

    matter = _staged(specialist)
    mark_sent(matter, specialist, [ministry])
    url = reverse("matters:matter_edit", kwargs={"pk": matter.pk})
    payload = edit_payload(matter, stage=str(_stage("in_force").pk))

    refused = signed_in.post(url, payload)

    assert refused.status_code == 400
    body = refused.content.decode()
    assert FOLLOW_UP_CLOSURE_WARNING in body
    assert 'name="confirm_follow_up_closure"' in body
    matter.refresh_from_db()
    assert matter.is_open

    confirmed = signed_in.post(url, {**payload, "confirm_follow_up_closure": "on"})

    assert confirmed.status_code == 302
    matter.refresh_from_db()
    assert not matter.is_open


def test_the_header_stage_asks_before_closing_and_closes_once_confirmed(
    signed_in, specialist, ministry
):
    matter = _staged(specialist)
    mark_sent(matter, specialist, [ministry])
    url = reverse("matters:update_field", kwargs={"pk": matter.pk, "field": "stage"})
    stage = str(_stage("monitoring_stopped").pk)

    refused = signed_in.post(url, {"stage": stage}, headers={"HX-Request": "true"})

    assert refused.status_code == 400
    body = refused.content.decode()
    assert FOLLOW_UP_CLOSURE_WARNING in body
    assert "Sulge teema ja lõpeta ka järelkontroll" in body
    matter.refresh_from_db()
    assert matter.is_open

    confirmed = signed_in.post(
        url, {"stage": stage, "confirm_follow_up_closure": "on"}, headers={"HX-Request": "true"}
    )

    assert confirmed.status_code == 200
    matter.refresh_from_db()
    assert not matter.is_open


def test_finishing_the_current_step_into_a_closing_stage_asks_first(
    signed_in, specialist, ministry
):
    matter = _staged(specialist)
    mark_sent(matter, specialist, [ministry])
    current = set_next_action_for_new_work(
        matter=matter, text="Loe eelnõu", target_date=day(3), actor=specialist
    )
    data = {"action_id": str(current.pk), "body": "Valmis.", "stage": str(_stage("in_force").pk)}

    refused = post(signed_in, "complete_current_action", matter, data)

    assert refused.status_code == 400
    zone = _zone(refused.content.decode())
    assert FOLLOW_UP_CLOSURE_WARNING in zone
    assert 'name="confirm_follow_up_closure"' in zone
    current.refresh_from_db()
    assert current.status == ActionStatus.OPEN

    confirmed = post(
        signed_in, "complete_current_action", matter, {**data, "confirm_follow_up_closure": "on"}
    )

    assert confirmed.status_code == 200
    matter.refresh_from_db()
    assert not matter.is_open


def test_a_koja_arvamus_that_closes_the_matter_asks_first(signed_in, specialist, ministry):
    matter = _staged(specialist)
    url = reverse("matters:add_koda_opinion", kwargs={"pk": matter.pk})

    def payload(**extra):
        return {
            "upload": pdf(),
            "recipients": [str(ministry.pk)],
            "sent_on": et(day(0)),
            "summary": "Viimane arvamus.",
            "stage": str(_stage("in_force").pk),
            **extra,
        }

    refused = signed_in.post(url, payload(), headers={"HX-Request": "true"})

    assert refused.status_code == 400
    assert FOLLOW_UP_CLOSURE_WARNING in refused.content.decode()
    assert not Submission.objects.filter(matter=matter).exists()

    confirmed = signed_in.post(
        url, payload(confirm_follow_up_closure="on"), headers={"HX-Request": "true"}
    )

    assert confirmed.status_code == 200
    matter.refresh_from_db()
    assert not matter.is_open
    assert Submission.objects.filter(matter=matter).count() == 1


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def test_the_send_row_carries_its_check(normal_matter, specialist, ministry):
    from app.matters.timeline import matter_timeline

    opinion = send_koja_arvamus(normal_matter, specialist, [ministry], day(0))

    items, _ = matter_timeline(matter=normal_matter, user=specialist, limit=100)

    (row,) = [item for item in items if getattr(item.record, "pk", None) == opinion.pk]
    assert row.next_step is not None
    assert row.next_step.text == FOLLOW_UP_TEXT_ONE
    assert not [
        item
        for item in items
        if item.record is None and item.next_step and item.next_step.text == FOLLOW_UP_TEXT_ONE
    ]


def test_a_check_reads_as_one_row_with_what_it_found(normal_matter, specialist, ministry):
    from app.matters import workspace
    from app.matters.timeline import matter_timeline

    check = active_check(mark_sent(normal_matter, specialist, [ministry]))
    workspace.check_opinion_follow_up(
        matter=normal_matter,
        author=specialist,
        action_id=check.pk,
        outcome=FollowUpOutcome.NO_RESPONSE,
        next_check_on=day(10),
        body="Helistasin, vastust veel pole.",
    )

    items, _ = matter_timeline(matter=normal_matter, user=specialist, limit=100)

    (row,) = [item for item in items if item.entry is not None]
    assert row.completion_text.startswith("Vastust ei ole — kontrollin uuesti")
    assert "Helistasin, vastust veel pole." in row.completion_text
    assert row.next_step is not None
    assert row.next_step.text == FOLLOW_UP_TEXT_ONE


def test_a_moved_day_is_in_the_change_log_and_not_a_chronology_row(
    normal_matter, specialist, ministry
):
    from app.matters.timeline import matter_timeline

    check = active_check(mark_sent(normal_matter, specialist, [ministry]))
    reschedule_check(matter=normal_matter, action_id=check.pk, target_date=day(4), actor=specialist)

    events = scope_change_events(
        ChangeEvent.objects.filter(event_type=ChangeEventType.NEXT_ACTION_RESCHEDULED), specialist
    )
    assert events.count() == 1
    items, _ = matter_timeline(matter=normal_matter, user=specialist, limit=100)
    assert not [
        item
        for item in items
        if item.event is not None
        and item.event.event_type == ChangeEventType.NEXT_ACTION_RESCHEDULED
    ]
