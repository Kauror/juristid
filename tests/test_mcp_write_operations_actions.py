"""MCP writes on the current action: `set_next_action` and `complete_action`.

The domain cases the owner's brief names: DO/WAIT/MONITOR with an explicit date
meaning and precision, a WAIT that names who is waited for and whose date is
never the opinion deadline, a new step that never silently replaces the current
one, the stale-tab rule, and a completion that asks exactly what the action's
kind needs — «what was done» for an ordinary step, the typed outcome for an
opinion follow-up check — never a bare «done».
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from django.utils import timezone
from pydantic import ValidationError

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.matters.models import Entry
from app.matters.services import close_matter
from app.mcp_writes.operations import REGISTRY
from app.workflow.enums import ActionKind, ActionStatus, DatePrecision, DateSemantics
from app.workflow.follow_ups import FOLLOW_UP_HAS_ITS_OWN_FORM, schedule_first_check
from app.workflow.models import NextAction, OpinionFollowUp
from app.workflow.services import (
    DEPARTED_OWNER_REFUSAL,
    add_planned_action,
    promote_next_planned_action,
    set_next_action,
)
from tests import factories
from tests.test_mcp_write_operations_support import (
    change,
    commit,
    execute_refusal,
    is_stale,
    prepare,
    preview_text,
    public_origin,  # noqa: F401
    refusal,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("public_origin")]


def _soon(days: int = 7) -> str:
    return (timezone.localdate() + timedelta(days=days)).isoformat()


def _current(matter):
    return NextAction.objects.get(matter=matter, status=ActionStatus.OPEN)


def _follow_up_check(matter, actor):
    submission = factories.send_opinion_through_services(matter, actor)
    schedule_first_check(submission=submission, actor=actor)
    check = promote_next_planned_action(matter=matter, actor=actor)
    assert check is not None and check.follow_up_id is not None
    return check


# ---------------------------------------------------------------------------
# set_next_action — new
# ---------------------------------------------------------------------------


def test_new_do_step_previews_and_writes_through_the_native_service(specialist, normal_matter):
    prepared = prepare(
        "set_next_action",
        specialist,
        matter=normal_matter.display_reference,
        mode="new",
        text="Saata arvamus ministeeriumile",
        kind="do",
        date=_soon(),
        date_precision="exact",
    )
    assert prepared.state["current_action"] is None
    assert change(prepared, "kind").proposed == "Teen"
    assert "üle aja" in preview_text(prepared)
    executed = commit("set_next_action", specialist, prepared)
    action = NextAction.objects.get(pk=executed.record_id)
    assert (action.kind, action.date_semantics, action.date_precision) == (
        "DO",
        "DEADLINE",
        "EXACT",
    )
    assert action.status == ActionStatus.OPEN
    assert action.responsible == specialist
    assert action.created_by == specialist
    assert (
        ChangeEvent.objects.filter(
            object_id=action.pk, event_type=ChangeEventType.NEXT_ACTION_SET
        ).count()
        == 1
    )


def test_a_wait_names_who_is_waited_for_and_is_not_the_opinion_deadline(specialist):
    deadline = timezone.localdate() + timedelta(days=20)
    matter = factories.MatterFactory(owner=specialist, response_deadline=deadline)
    prepared = prepare(
        "set_next_action",
        specialist,
        matter=str(matter.pk),
        mode="new",
        text="Ootan eelnõu uut versiooni",
        kind="wait",
        waiting_for="Rahandusministeerium",
        date=(timezone.localdate() + timedelta(days=30)).isoformat()[:7],
        date_precision="month",
        date_meaning="review_on",
    )
    text = preview_text(prepared)
    assert "mitte tähtaeg ega arvamuse tähtaeg" in text
    assert "jääb kehtima" in text
    assert prepared.canonical["text"] == "Ootan eelnõu uut versiooni (ootame: Rahandusministeerium)"
    executed = commit("set_next_action", specialist, prepared)
    action = NextAction.objects.get(pk=executed.record_id)
    assert (action.kind, action.date_semantics, action.date_precision) == (
        "WAIT",
        "REVIEW_ON",
        "MONTH",
    )
    assert action.target_date.day == 1  # the month's anchor, never an invented day
    matter.refresh_from_db()
    assert matter.response_deadline == deadline


def test_a_wait_needs_who_and_a_date_meaning(specialist, normal_matter):
    base = {"matter": str(normal_matter.pk), "mode": "new", "text": "Ootan", "kind": "wait"}
    assert refusal("set_next_action", specialist, **base).code == "missing_information"
    assert (
        refusal(
            "set_next_action",
            specialist,
            **base,
            waiting_for="MKM",
            date=_soon(),
            date_precision="exact",
        ).code
        == "missing_information"
    )
    # The party already named in the sentence is not appended again.
    prepared = prepare(
        "set_next_action", specialist, **{**base, "text": "Ootan MKM vastust"}, waiting_for="MKM"
    )
    assert prepared.canonical["text"] == "Ootan MKM vastust"


def test_monitor_expected_around_keeps_a_quarter(specialist, normal_matter):
    prepared = prepare(
        "set_next_action",
        specialist,
        matter=str(normal_matter.pk),
        mode="new",
        text="Jälgin Riigikogu menetlust",
        kind="monitor",
        date="2027-Q2",
        date_precision="quarter",
        date_meaning="expected_around",
    )
    executed = commit("set_next_action", specialist, prepared)
    action = NextAction.objects.get(pk=executed.record_id)
    assert (action.kind, action.date_semantics) == ("MONITOR", "EXPECTED_AROUND")
    assert (action.target_date, action.date_precision) == (date(2027, 4, 1), DatePrecision.QUARTER)


def test_an_undated_step_stays_undated(specialist, normal_matter):
    prepared = prepare(
        "set_next_action",
        specialist,
        matter=str(normal_matter.pk),
        mode="new",
        text="Uurida",
        kind="do",
    )
    executed = commit("set_next_action", specialist, prepared)
    action = NextAction.objects.get(pk=executed.record_id)
    assert action.target_date is None
    assert action.date_precision == DatePrecision.EXACT


def test_new_never_replaces_the_current_step(specialist, normal_matter):
    set_next_action(matter=normal_matter, text="Olemasolev", actor=specialist)
    refused = refusal(
        "set_next_action",
        specialist,
        matter=str(normal_matter.pk),
        mode="new",
        text="Uus",
        kind="do",
    )
    assert refused.code == "domain_rule"
    assert NextAction.objects.filter(matter=normal_matter).count() == 1


def test_new_goes_stale_when_a_step_appears(specialist, normal_matter):
    prepared = prepare(
        "set_next_action",
        specialist,
        matter=str(normal_matter.pk),
        mode="new",
        text="Uus",
        kind="do",
    )
    set_next_action(matter=normal_matter, text="Kolleegi samm", actor=specialist)
    assert is_stale("set_next_action", specialist, prepared)
    assert execute_refusal("set_next_action", specialist, prepared).code == "stale"


def test_new_work_is_refused_for_a_departed_owner(specialist):
    departed = factories.UserFactory(is_active=False)
    matter = factories.MatterFactory(owner=departed)
    refused = refusal(
        "set_next_action", specialist, matter=str(matter.pk), mode="new", text="Samm", kind="do"
    )
    assert (refused.code, refused.message) == ("domain_rule", DEPARTED_OWNER_REFUSAL)


def test_date_arguments_are_explicit(specialist, normal_matter):
    model = REGISTRY["set_next_action"].arguments
    base = {"matter": "2026_1", "mode": "new", "text": "x", "kind": "do"}
    with pytest.raises(ValidationError):
        model.model_validate({**base, "date": "2026-11-01"})  # no precision
    with pytest.raises(ValidationError):
        model.model_validate(
            {**base, "date": "2026-11-01", "date_precision": "exact", "date_meaning": "review_on"}
        )
    with pytest.raises(ValidationError):
        model.model_validate({**base, "kind": "wait", "date_meaning": "planned_day"})
    with pytest.raises(ValidationError):
        model.model_validate({**base, "kind": "sleep"})
    with pytest.raises(ValidationError):
        model.model_validate({**base, "text": "x" * 2001})
    with pytest.raises(ValidationError):
        model.model_validate({**base, "waiting_for": "MKM"})
    # A month written as a day is refused rather than read as a day.
    assert (
        refusal(
            "set_next_action",
            specialist,
            **{**base, "matter": str(normal_matter.pk)},
            date="2026-11-15",
            date_precision="month",
        ).code
        == "invalid_argument"
    )


def test_set_next_action_refusals(specialist, reader, administrator, normal_matter):
    args = {"matter": str(normal_matter.pk), "mode": "new", "text": "x", "kind": "do"}
    assert refusal("set_next_action", reader, **args).code == "forbidden"
    assert refusal("set_next_action", administrator, **args).code == "forbidden"
    assert (
        refusal("set_next_action", specialist, **{**args, "matter": str(uuid.uuid4())}).code
        == "not_found"
    )
    close_matter(matter=normal_matter, disposition="OTHER", actor=specialist)
    assert refusal("set_next_action", specialist, **args).code == "domain_rule"


# ---------------------------------------------------------------------------
# set_next_action — change_current
# ---------------------------------------------------------------------------


def test_change_current_supersedes_with_history(specialist, normal_matter):
    old = set_next_action(matter=normal_matter, text="Vana sõnastus", actor=specialist)
    prepared = prepare(
        "set_next_action",
        specialist,
        matter=str(normal_matter.pk),
        mode="change_current",
        current_action_id=str(old.pk),
        text="Uus sõnastus",
        kind="do",
        date=_soon(3),
        date_precision="exact",
    )
    text_change = change(prepared, "text")
    assert (text_change.current, text_change.proposed) == ("Vana sõnastus", "Uus sõnastus")
    assert prepared.state["current_action"]["id"] == str(old.pk)
    executed = commit("set_next_action", specialist, prepared)
    old.refresh_from_db()
    assert old.status == ActionStatus.SUPERSEDED
    assert str(old.replaced_by_id) == executed.record_id


def test_change_current_names_the_step_and_goes_stale(specialist, normal_matter):
    old = set_next_action(matter=normal_matter, text="Vana", actor=specialist)
    assert (
        refusal(
            "set_next_action",
            specialist,
            matter=str(normal_matter.pk),
            mode="change_current",
            current_action_id=str(uuid.uuid4()),
            text="x",
            kind="do",
        ).code
        == "stale"
    )
    prepared = prepare(
        "set_next_action",
        specialist,
        matter=str(normal_matter.pk),
        mode="change_current",
        current_action_id=str(old.pk),
        text="Minu",
        kind="do",
    )
    set_next_action(matter=normal_matter, text="Kolleegi uus", actor=specialist)
    assert is_stale("set_next_action", specialist, prepared)
    assert execute_refusal("set_next_action", specialist, prepared).code == "stale"


def test_change_current_without_a_step_or_on_a_follow_up_check(specialist, normal_matter):
    assert (
        refusal(
            "set_next_action",
            specialist,
            matter=str(normal_matter.pk),
            mode="change_current",
            text="x",
            kind="do",
        ).code
        == "domain_rule"
    )
    _follow_up_check(normal_matter, specialist)
    refused = refusal(
        "set_next_action",
        specialist,
        matter=str(normal_matter.pk),
        mode="change_current",
        text="x",
        kind="do",
    )
    assert (refused.code, refused.message) == ("domain_rule", FOLLOW_UP_HAS_ITS_OWN_FORM)


# ---------------------------------------------------------------------------
# complete_action — ordinary
# ---------------------------------------------------------------------------


def test_complete_requires_what_was_done(specialist, normal_matter):
    set_next_action(matter=normal_matter, text="Koostada arvamus", actor=specialist)
    refused = refusal("complete_action", specialist, matter=str(normal_matter.pk))
    assert refused.code == "missing_information"
    assert (
        refusal(
            "complete_action",
            specialist,
            matter=str(normal_matter.pk),
            follow_up_outcome="response_received",
        ).code
        == "invalid_argument"
    )


def test_complete_writes_the_note_and_the_completion(specialist, normal_matter):
    current = set_next_action(matter=normal_matter, text="Koostada arvamus", actor=specialist)
    entries = Entry.objects.count()
    prepared = prepare(
        "complete_action",
        specialist,
        matter=normal_matter.display_reference,
        action_id=str(current.pk),
        what_was_done="Arvamus koostatud ja kooskõlastatud.",
        next_step_text="Saata arvamus",
        next_step_on=_soon(2),
    )
    assert Entry.objects.count() == entries
    assert prepared.preview.target.record_id == str(current.pk)
    assert "Teemat ei suleta" in preview_text(prepared)
    executed = commit("complete_action", specialist, prepared)
    current.refresh_from_db()
    assert current.status == ActionStatus.COMPLETED
    assert executed.saved["status"] == ActionStatus.COMPLETED
    note = Entry.objects.get(pk=executed.saved["entry_id"])
    assert "Arvamus koostatud" in note.body
    following = NextAction.objects.get(pk=executed.saved["next_action_id"])
    assert (following.text, following.status, following.kind) == ("Saata arvamus", "OPEN", "DO")
    assert (
        ChangeEvent.objects.filter(
            object_id=current.pk, event_type=ChangeEventType.NEXT_ACTION_COMPLETED
        ).count()
        == 1
    )
    normal_matter.refresh_from_db()
    assert normal_matter.is_open


def test_complete_previews_the_promotion_of_a_planned_step(specialist, normal_matter):
    set_next_action(matter=normal_matter, text="Praegune", actor=specialist)
    add_planned_action(
        matter=normal_matter,
        text="Hilisem",
        target_date=timezone.localdate() + timedelta(days=9),
        actor=specialist,
    )
    prepared = prepare(
        "complete_action", specialist, matter=str(normal_matter.pk), what_was_done="Tehtud nii."
    )
    assert "«Hilisem»" in preview_text(prepared)
    commit("complete_action", specialist, prepared)
    assert _current(normal_matter).text == "Hilisem"


def test_complete_goes_stale_when_the_step_changes(specialist, normal_matter):
    set_next_action(matter=normal_matter, text="A", actor=specialist)
    prepared = prepare(
        "complete_action", specialist, matter=str(normal_matter.pk), what_was_done="x"
    )
    set_next_action(matter=normal_matter, text="B", actor=specialist)
    assert is_stale("complete_action", specialist, prepared)
    assert execute_refusal("complete_action", specialist, prepared).code == "stale"
    assert _current(normal_matter).text == "B"


def test_complete_without_a_current_step(specialist, normal_matter, reader):
    assert refusal(
        "complete_action", specialist, matter=str(normal_matter.pk), what_was_done="x"
    ).code == ("domain_rule")
    set_next_action(matter=normal_matter, text="A", actor=specialist)
    assert refusal(
        "complete_action", reader, matter=str(normal_matter.pk), what_was_done="x"
    ).code == ("forbidden")


def test_complete_arguments_are_consistent():
    model = REGISTRY["complete_action"].arguments
    with pytest.raises(ValidationError):
        model.model_validate(
            {"matter": "2026_1", "what_was_done": "x", "next_step_on": "2026-12-01"}
        )
    with pytest.raises(ValidationError):
        model.model_validate({"matter": "2026_1", "follow_up_outcome": "done"})
    with pytest.raises(ValidationError):
        model.model_validate(
            {
                "matter": "2026_1",
                "follow_up_outcome": "response_received",
                "next_check_on": "2026-12-01",
            }
        )
    with pytest.raises(ValidationError):
        model.model_validate({"matter": "2026_1", "done": True})


# ---------------------------------------------------------------------------
# complete_action — an opinion follow-up check
# ---------------------------------------------------------------------------


def test_a_follow_up_check_needs_its_outcome(specialist, normal_matter):
    _follow_up_check(normal_matter, specialist)
    refused = refusal(
        "complete_action", specialist, matter=str(normal_matter.pk), what_was_done="Tehtud"
    )
    assert refused.code == "missing_information"
    assert "response_received" in refused.message
    assert (
        refusal(
            "complete_action",
            specialist,
            matter=str(normal_matter.pk),
            follow_up_outcome="no_response",
        ).code
        == "missing_information"
    )
    assert (
        refusal(
            "complete_action",
            specialist,
            matter=str(normal_matter.pk),
            follow_up_outcome="monitoring_ended",
        ).code
        == "missing_information"
    )
    assert (
        refusal(
            "complete_action",
            specialist,
            matter=str(normal_matter.pk),
            follow_up_outcome="no_response",
            next_check_on=(timezone.localdate() - timedelta(days=1)).isoformat(),
        ).code
        == "domain_rule"
    )


def test_no_response_schedules_the_next_check_on_the_chosen_day(specialist, normal_matter):
    check = _follow_up_check(normal_matter, specialist)
    day = timezone.localdate() + timedelta(days=14)
    prepared = prepare(
        "complete_action",
        specialist,
        matter=str(normal_matter.pk),
        follow_up_outcome="no_response",
        next_check_on=day.isoformat(),
    )
    assert change(prepared, "outcome").proposed == "Vastust ei ole — kontrollin uuesti"
    commit("complete_action", specialist, prepared)
    check.refresh_from_db()
    assert (check.status, check.follow_up_outcome) == (ActionStatus.COMPLETED, "NO_RESPONSE")
    following = NextAction.objects.get(follow_up_id=check.follow_up_id, status=ActionStatus.OPEN)
    assert following.target_date == day


def test_response_received_ends_the_monitoring(specialist, normal_matter):
    check = _follow_up_check(normal_matter, specialist)
    prepared = prepare(
        "complete_action",
        specialist,
        matter=str(normal_matter.pk),
        follow_up_outcome="response_received",
        what_was_done="Ministeerium vastas 3.11.",
    )
    commit("complete_action", specialist, prepared)
    follow_up = OpinionFollowUp.objects.get(pk=check.follow_up_id)
    assert follow_up.state == "RESPONSE_RECEIVED"
    note = Entry.objects.filter(matter=normal_matter).order_by("-created_at").first()
    assert (
        note is not None and "Vastus saabunud." in note.body and "Ministeerium vastas" in note.body
    )
    assert not NextAction.objects.filter(
        matter=normal_matter, kind=ActionKind.DO, status="OPEN", follow_up=follow_up
    ).exists()
    assert DateSemantics.DEADLINE  # vocabulary unchanged
