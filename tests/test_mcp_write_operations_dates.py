"""MCP writes on dates: `response_deadline` (new request vs moved) and `add_important_date`.

The response-deadline cases are the ones a wrong reading would turn into a
false record: a new request recorded as a move (the old request silently
«continued»), a move recorded as a new request (an obligation silently
discharged), an opinion sent for the earlier request counted as the answer to
the new one, or a round opened without anybody saying so.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from django.utils import timezone
from pydantic import ValidationError

from app.audit.enums import ChangeEventType
from app.audit.models import ChangeEvent
from app.intelligence.models import MatterEffectiveDate, MatterImportantDate
from app.matters import date_order
from app.matters.models import MatterResponseDeadline
from app.matters.response_deadlines import (
    ANSWER_NEEDS_A_BASIS,
    LEGACY_DISCHARGE_NOTE,
    NO_CURRENT_DEADLINE,
    change_response_deadline,
    request_response_deadline,
)
from app.matters.services import close_matter
from app.matters.work_items import response_obligation_of
from app.mcp_writes.operations import REGISTRY
from app.notifications.models import CommencementReminder
from app.workflow.enums import Track
from app.workflow.models import NextAction
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


def _in(days: int) -> date:
    return timezone.localdate() + timedelta(days=days)


# ---------------------------------------------------------------------------
# response_deadline — new_request
# ---------------------------------------------------------------------------


def test_a_first_request_opens_one_request_and_nothing_else(specialist, normal_matter):
    prepared = prepare(
        "response_deadline",
        specialist,
        matter=normal_matter.display_reference,
        mode="new_request",
        deadline=_in(14).isoformat(),
    )
    assert prepared.state["response_deadline"] is None
    assert change(prepared, "meaning").proposed == "Uus arvamuse küsimine"
    assert "Koja arvamust ei märgita saadetuks" in preview_text(prepared)
    commit("response_deadline", specialist, prepared)
    normal_matter.refresh_from_db()
    assert normal_matter.response_deadline == _in(14)
    assert normal_matter.response_requested_at is not None
    assert not MatterResponseDeadline.objects.filter(matter=normal_matter).exists()
    assert not NextAction.objects.filter(matter=normal_matter).exists()


def test_an_opinion_sent_earlier_does_not_answer_the_new_request(specialist, normal_matter):
    factories.send_opinion_through_services(normal_matter, specialist)
    prepared = prepare(
        "response_deadline",
        specialist,
        matter=str(normal_matter.pk),
        mode="new_request",
        deadline=_in(10).isoformat(),
    )
    assert "enne seda saadetud arvamus seda ei vasta" in preview_text(prepared)
    commit("response_deadline", specialist, prepared)
    normal_matter.refresh_from_db()
    assert response_obligation_of(normal_matter, specialist).is_outstanding


def test_a_new_request_over_a_current_one_needs_the_old_ones_outcome(specialist, normal_matter):
    request_response_deadline(matter=normal_matter, deadline=_in(5), actor=specialist)
    normal_matter.refresh_from_db()
    asked = refusal(
        "response_deadline",
        specialist,
        matter=str(normal_matter.pk),
        mode="new_request",
        deadline=_in(30).isoformat(),
    )
    assert asked.code == "missing_information"
    assert "current_request_outcome" in asked.message
    answered_without_basis = refusal(
        "response_deadline",
        specialist,
        matter=str(normal_matter.pk),
        mode="new_request",
        deadline=_in(30).isoformat(),
        current_request_outcome="answered",
    )
    assert (answered_without_basis.code, answered_without_basis.message) == (
        "missing_information",
        ANSWER_NEEDS_A_BASIS,
    )
    first_requested = normal_matter.response_requested_at
    prepared = prepare(
        "response_deadline",
        specialist,
        matter=str(normal_matter.pk),
        mode="new_request",
        deadline=_in(30).isoformat(),
        current_request_outcome="superseded",
    )
    assert "lõpetatakse ajaloos" in preview_text(prepared)
    commit("response_deadline", specialist, prepared)
    normal_matter.refresh_from_db()
    history = MatterResponseDeadline.objects.get(matter=normal_matter)
    assert (history.deadline, history.outcome, history.next_deadline) == (
        _in(5),
        "SUPERSEDED",
        _in(30),
    )
    assert normal_matter.response_deadline == _in(30)
    assert normal_matter.response_requested_at > first_requested


def test_a_legacy_deadline_answered_under_the_old_rules_is_said_so(specialist):
    matter = factories.MatterFactory(owner=specialist, response_deadline=_in(-20))
    factories.send_opinion_through_services(matter, specialist)
    matter.refresh_from_db()
    assert matter.response_requested_at is None
    prepared = prepare(
        "response_deadline",
        specialist,
        matter=str(matter.pk),
        mode="new_request",
        deadline=_in(15).isoformat(),
    )
    assert LEGACY_DISCHARGE_NOTE in preview_text(prepared)
    commit("response_deadline", specialist, prepared)
    history = MatterResponseDeadline.objects.get(matter=matter)
    assert (history.outcome, history.note) == ("ANSWERED", LEGACY_DISCHARGE_NOTE)


def test_a_chamber_initiative_has_no_new_request(specialist):
    matter = factories.MatterFactory(owner=specialist, track=Track.KODA_INITIATIVE)
    assert (
        refusal(
            "response_deadline",
            specialist,
            matter=str(matter.pk),
            mode="new_request",
            deadline=_in(3).isoformat(),
        ).code
        == "domain_rule"
    )


# ---------------------------------------------------------------------------
# response_deadline — move
# ---------------------------------------------------------------------------


def test_move_keeps_the_request_and_records_the_old_date(specialist, normal_matter):
    request_response_deadline(matter=normal_matter, deadline=_in(5), actor=specialist)
    normal_matter.refresh_from_db()
    requested = normal_matter.response_requested_at
    prepared = prepare(
        "response_deadline",
        specialist,
        matter=str(normal_matter.pk),
        mode="move",
        deadline=_in(12).isoformat(),
        note="Ministeerium pikendas.",
    )
    current = change(prepared, "response_deadline")
    assert current.current and current.proposed
    assert "Uut arvamuse küsimist ei avata" in preview_text(prepared)
    assert prepared.state["response_deadline"] == _in(5).isoformat()
    commit("response_deadline", specialist, prepared)
    normal_matter.refresh_from_db()
    assert normal_matter.response_deadline == _in(12)
    assert normal_matter.response_requested_at == requested
    moved = MatterResponseDeadline.objects.get(matter=normal_matter)
    assert (moved.outcome, moved.deadline, moved.note) == (
        "MOVED",
        _in(5),
        "Ministeerium pikendas.",
    )
    assert (
        ChangeEvent.objects.filter(
            matter=normal_matter, event_type=ChangeEventType.RESPONSE_DEADLINE_ENDED
        ).count()
        == 1
    )


def test_move_needs_a_current_deadline_and_a_different_date(specialist, normal_matter):
    refused = refusal(
        "response_deadline",
        specialist,
        matter=str(normal_matter.pk),
        mode="move",
        deadline=_in(5).isoformat(),
    )
    assert (refused.code, refused.message) == ("domain_rule", NO_CURRENT_DEADLINE)
    request_response_deadline(matter=normal_matter, deadline=_in(5), actor=specialist)
    assert (
        refusal(
            "response_deadline",
            specialist,
            matter=str(normal_matter.pk),
            mode="move",
            deadline=_in(5).isoformat(),
        ).code
        == "invalid_argument"
    )


def test_a_deadline_changed_meanwhile_is_stale(specialist, normal_matter):
    request_response_deadline(matter=normal_matter, deadline=_in(5), actor=specialist)
    prepared = prepare(
        "response_deadline",
        specialist,
        matter=str(normal_matter.pk),
        mode="move",
        deadline=_in(9).isoformat(),
    )
    normal_matter.refresh_from_db()
    change_response_deadline(
        matter=normal_matter, deadline=_in(7), change="MOVED", actor=specialist
    )
    assert is_stale("response_deadline", specialist, prepared)
    # The service's own revision check refuses too.
    assert execute_refusal("response_deadline", specialist, prepared).code == "domain_rule"
    normal_matter.refresh_from_db()
    assert normal_matter.response_deadline == _in(7)


def test_deadline_warnings_and_refusals(specialist, reader, normal_matter):
    matter = factories.MatterFactory(owner=specialist, received_date=_in(-1))
    prepared = prepare(
        "response_deadline",
        specialist,
        matter=str(matter.pk),
        mode="new_request",
        deadline=_in(-3).isoformat(),
    )
    assert date_order.DEADLINE_BEFORE_RECEIVED in prepared.preview.warnings
    assert "Tähtaeg on minevikus." in prepared.preview.warnings
    args = {"matter": str(normal_matter.pk), "mode": "new_request", "deadline": _in(3).isoformat()}
    assert refusal("response_deadline", reader, **args).code == "forbidden"
    assert (
        refusal("response_deadline", specialist, **{**args, "matter": str(uuid.uuid4())}).code
        == "not_found"
    )
    close_matter(matter=normal_matter, disposition="OTHER", actor=specialist)
    assert refusal("response_deadline", specialist, **args).code == "domain_rule"
    model = REGISTRY["response_deadline"].arguments
    with pytest.raises(ValidationError):
        model.model_validate({**args, "mode": "correct"})
    with pytest.raises(ValidationError):
        model.model_validate({**args, "mode": "move", "current_request_outcome": "answered"})
    with pytest.raises(ValidationError):
        model.model_validate({**args, "deadline": "2026-12-01T00:00:00"})


# ---------------------------------------------------------------------------
# add_important_date
# ---------------------------------------------------------------------------


def test_important_date_exact(specialist, normal_matter):
    prepared = prepare(
        "add_important_date",
        specialist,
        matter=normal_matter.display_reference,
        title="Kooskõlastusringi lõpp",
        date=_in(20).isoformat(),
        date_precision="exact",
    )
    assert "Teavitust ei saadeta" in preview_text(prepared)
    executed = commit("add_important_date", specialist, prepared)
    record = MatterImportantDate.objects.get(pk=executed.record_id)
    assert (record.date_value, record.period_end, record.date_precision, record.kind) == (
        _in(20),
        _in(20),
        "EXACT",
        "OTHER",
    )
    assert (
        ChangeEvent.objects.filter(
            object_id=record.pk, event_type=ChangeEventType.IMPORTANT_DATE_ADDED
        ).count()
        == 1
    )
    assert not NextAction.objects.filter(matter=normal_matter).exists()


def test_an_approximate_date_keeps_its_precision(specialist, normal_matter):
    prepared = prepare(
        "add_important_date",
        specialist,
        matter=str(normal_matter.pk),
        title="Uus versioon",
        date="2027-Q1",
        date_precision="quarter",
    )
    assert change(prepared, "date").proposed == "I kvartal 2027"
    executed = commit("add_important_date", specialist, prepared)
    record = MatterImportantDate.objects.get(pk=executed.record_id)
    assert (record.date_value, record.period_end, record.date_precision) == (
        date(2027, 1, 1),
        date(2027, 3, 31),
        "QUARTER",
    )
    year = prepare(
        "add_important_date",
        specialist,
        matter=str(normal_matter.pk),
        title="T",
        date="2028",
        date_precision="year",
    )
    assert year.canonical["period_end"] == "2028-12-31"
    assert (
        refusal(
            "add_important_date",
            specialist,
            matter=str(normal_matter.pk),
            title="T",
            date="2027-03-15",
            date_precision="quarter",
        ).code
        == "invalid_argument"
    )


def test_an_estimated_commencement_stays_an_estimate(specialist, normal_matter):
    prepared = prepare(
        "add_important_date",
        specialist,
        matter=str(normal_matter.pk),
        title="Seadus jõustub eeldatavasti",
        date=_in(40).isoformat()[:7],
        date_precision="month",
        kind="estimated_commencement",
        note="Allikas: ministeeriumi pressiteade",
    )
    text = preview_text(prepared)
    assert "mitte kinnitatud jõustumisena" in text
    assert "jõustumise meeldetuletust ei teki" in text
    executed = commit("add_important_date", specialist, prepared)
    record = MatterImportantDate.objects.get(pk=executed.record_id)
    assert (record.kind, record.date_precision) == ("ESTIMATED_COMMENCEMENT", "MONTH")
    assert not MatterEffectiveDate.objects.filter(matter=normal_matter).exists()
    assert not CommencementReminder.objects.exists()


def test_important_date_refusals(specialist, reader, normal_matter):
    args = {
        "matter": str(normal_matter.pk),
        "title": "T",
        "date": _in(2).isoformat(),
        "date_precision": "exact",
    }
    assert refusal("add_important_date", reader, **args).code == "forbidden"
    prepared = prepare("add_important_date", specialist, **args)
    close_matter(matter=normal_matter, disposition="OTHER", actor=specialist)
    assert is_stale("add_important_date", specialist, prepared)
    assert execute_refusal("add_important_date", specialist, prepared).code == "domain_rule"
    assert not MatterImportantDate.objects.filter(matter=normal_matter).exists()
    model = REGISTRY["add_important_date"].arguments
    with pytest.raises(ValidationError):
        model.model_validate({**args, "date_precision": "half_year"})
    with pytest.raises(ValidationError):
        model.model_validate({**args, "kind": "commencement"})
    with pytest.raises(ValidationError):
        model.model_validate({**args, "title": "x" * 2001})
